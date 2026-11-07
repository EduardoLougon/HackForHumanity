"""Profiles Generation Module (Stage 5).

Aggregates the 15-minute occupancy grid into recurrent weekly profiles (pole_id, dow, bucket)
using exponential recency weighting over the trailing window, excluding California holidays.
Computes sub-area fallbacks, p_available, confidence ratings, and flags out-of-service meters.
Outputs: data/clean/profiles.parquet and updates status in data/clean/meters.parquet.
"""

import json
import logging
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Set
import duckdb
import holidays
import yaml

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

CLEAN_DIR = Path("data/clean")
CONFIG_PATH = Path("config.yaml")

METERS_PARQUET = CLEAN_DIR / "meters.parquet"
OCCUPANCY_PARQUET = CLEAN_DIR / "occupancy.parquet"
TRANS_PARQUET = CLEAN_DIR / "transactions.parquet"
PROFILES_PARQUET = CLEAN_DIR / "profiles.parquet"


def load_config() -> Dict[str, Any]:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_california_holidays(start_year: int, end_year: int) -> Set[str]:
    """Get list of California public holiday ISO date strings."""
    ca_holidays = holidays.US(years=list(range(start_year, end_year + 1)), subdiv="CA")
    return {d.isoformat() for d in ca_holidays.keys()}


def run_profiles():
    CLEAN_DIR.mkdir(parents=True, exist_ok=True)
    cfg = load_config()

    profile_weeks = cfg.get("profile_window_weeks", 26)
    half_life_weeks = cfg.get("recency_half_life_weeks", 8)
    half_life_days = half_life_weeks * 7.0
    decay_lambda = math.log(2) / half_life_days

    min_dates_pole = cfg.get("min_dates_pole", 6)
    inactive_days = cfg.get("inactive_days", 14)
    unpaid_uplift = cfg.get("unpaid_occupancy_uplift", 0.0)

    likely_thresh = cfg.get("bands", {}).get("likely", 0.6)
    possible_thresh = cfg.get("bands", {}).get("possible", 0.3)

    con = duckdb.connect()

    # 1. Identify max date in occupancy grid
    max_ts = con.execute(f"SELECT max(bucket_start) FROM read_parquet('{OCCUPANCY_PARQUET.as_posix()}')").fetchone()[0]
    max_date = max_ts.date()
    min_date = max_date - timedelta(weeks=profile_weeks)
    logger.info(f"Generating availability profiles from {min_date} to {max_date}...")

    # 2. Exclude California holidays
    holidays_set = get_california_holidays(min_date.year, max_date.year)
    holidays_sql_list = ", ".join(f"'{h}'::DATE" for h in sorted(holidays_set))
    con.execute(f"""
        CREATE TEMP TABLE holiday_dates AS
        SELECT unnest([{holidays_sql_list}]) AS holiday_date
    """)

    # 3. Detect Out-of-Service Poles (0 transactions in last inactive_days while sub-area had activity)
    logger.info("Detecting possibly out-of-service meters...")
    cutoff_ts = max_ts - timedelta(days=inactive_days)

    con.execute(f"""
        CREATE TEMP TABLE recent_activity AS
        SELECT 
            m.pole_id,
            m.sub_area,
            COUNT(t.uuid) AS recent_pole_tx
        FROM read_parquet('{METERS_PARQUET.as_posix()}') m
        LEFT JOIN read_parquet('{TRANS_PARQUET.as_posix()}') t 
            ON m.pole_id = t.pole_id AND t.trans_start >= '{cutoff_ts}'
        GROUP BY m.pole_id, m.sub_area
    """)

    con.execute("""
        CREATE TEMP TABLE sub_area_activity AS
        SELECT sub_area, SUM(recent_pole_tx) AS recent_sub_area_tx
        FROM recent_activity
        GROUP BY sub_area
    """)

    con.execute("""
        CREATE TEMP TABLE out_of_service_poles AS
        SELECT r.pole_id
        FROM recent_activity r
        JOIN sub_area_activity s ON r.sub_area = s.sub_area
        WHERE r.recent_pole_tx = 0 AND s.recent_sub_area_tx >= 10
    """)
    oos_count = con.execute("SELECT count(*) FROM out_of_service_poles").fetchone()[0]
    logger.info(f"Detected {oos_count} possibly out-of-service meters.")

    # Update clean_meters table with updated status
    con.execute(f"""
        CREATE TEMP TABLE updated_meters AS
        SELECT 
            m.pole_id, m.lat, m.lon, m.zone, m.area, m.sub_area,
            m.meter_type, m.rate_per_hour, m.max_minutes, m.enforced_hours_json,
            CASE WHEN o.pole_id IS NOT NULL THEN 'possibly_out_of_service' ELSE m.status END AS status
        FROM read_parquet('{METERS_PARQUET.as_posix()}') m
        LEFT JOIN out_of_service_poles o ON m.pole_id = o.pole_id
    """)
    con.execute(f"COPY updated_meters TO '{METERS_PARQUET.as_posix()}' (FORMAT PARQUET)")

    # 4. Filter grid to non-holiday enforced dates and add recency weight
    logger.info("Computing recency weights and daily bucket indexes...")
    con.execute(f"""
        CREATE TEMP TABLE weighted_grid AS
        SELECT 
            o.pole_id,
            m.sub_area,
            dayofweek(o.bucket_start) AS dow, -- 0=Sun, 1=Mon, ..., 6=Sat
            (hour(o.bucket_start) * 4 + minute(o.bucket_start) / 15)::INTEGER AS bucket,
            o.bucket_start::DATE AS dt,
            o.occupied_fraction,
            exp(-{decay_lambda} * date_diff('day', o.bucket_start::DATE, '{max_date}'::DATE)) AS weight
        FROM read_parquet('{OCCUPANCY_PARQUET.as_posix()}') o
        INNER JOIN updated_meters m ON o.pole_id = m.pole_id
        LEFT JOIN holiday_dates h ON o.bucket_start::DATE = h.holiday_date
        WHERE h.holiday_date IS NULL
    """)

    # 5. Pole-level profiles
    logger.info("Aggregating pole-level profiles...")
    con.execute("""
        CREATE TEMP TABLE pole_profiles AS
        SELECT 
            pole_id,
            dow,
            bucket,
            COUNT(DISTINCT dt)::INTEGER AS n_dates,
            SUM(weight * occupied_fraction) / SUM(weight) AS mean_occupancy,
            SQRT(GREATEST(0, (SUM(weight * occupied_fraction * occupied_fraction) / SUM(weight)) - 
                             POW(SUM(weight * occupied_fraction) / SUM(weight), 2))) AS std_occupancy
        FROM weighted_grid
        GROUP BY pole_id, dow, bucket
    """)

    # 6. Sub-area fallback profiles
    logger.info("Aggregating sub-area fallback profiles...")
    con.execute("""
        CREATE TEMP TABLE sub_area_profiles AS
        SELECT 
            sub_area,
            dow,
            bucket,
            COUNT(DISTINCT dt)::INTEGER AS n_dates,
            SUM(weight * occupied_fraction) / SUM(weight) AS mean_occupancy,
            SQRT(GREATEST(0, (SUM(weight * occupied_fraction * occupied_fraction) / SUM(weight)) - 
                             POW(SUM(weight * occupied_fraction) / SUM(weight), 2))) AS std_occupancy
        FROM weighted_grid
        GROUP BY sub_area, dow, bucket
    """)

    # 7. Blend pole profiles with fallback, calculate p_available, confidence, and bands
    logger.info("Blending profiles and computing confidence bands...")
    con.execute(f"""
        CREATE TEMP TABLE final_profiles AS
        SELECT 
            m.pole_id,
            p.dow,
            p.bucket,
            -- p_available
            GREATEST(0.0, LEAST(1.0, 1.0 - 
                CASE 
                    WHEN p.n_dates >= {min_dates_pole} THEN p.mean_occupancy 
                    ELSE COALESCE(s.mean_occupancy, p.mean_occupancy) 
                END - {unpaid_uplift}
            )) AS p_available,
            -- band
            CASE 
                WHEN (1.0 - CASE WHEN p.n_dates >= {min_dates_pole} THEN p.mean_occupancy ELSE COALESCE(s.mean_occupancy, p.mean_occupancy) END - {unpaid_uplift}) >= {likely_thresh} THEN 'likely'
                WHEN (1.0 - CASE WHEN p.n_dates >= {min_dates_pole} THEN p.mean_occupancy ELSE COALESCE(s.mean_occupancy, p.mean_occupancy) END - {unpaid_uplift}) >= {possible_thresh} THEN 'possible'
                ELSE 'unlikely'
            END AS band,
            -- confidence
            CASE 
                WHEN p.n_dates >= 12 THEN 'high'
                WHEN p.n_dates >= {min_dates_pole} THEN 'medium'
                WHEN COALESCE(s.n_dates, 0) >= 12 THEN 'medium'
                ELSE 'low'
            END AS confidence,
            -- basis
            CASE 
                WHEN p.n_dates >= {min_dates_pole} THEN 'pole'
                ELSE 'sub_area'
            END AS basis,
            p.n_dates,
            ROUND(CASE WHEN p.n_dates >= {min_dates_pole} THEN p.mean_occupancy ELSE COALESCE(s.mean_occupancy, p.mean_occupancy) END, 4) AS mean_occupancy,
            ROUND(CASE WHEN p.n_dates >= {min_dates_pole} THEN p.std_occupancy ELSE COALESCE(s.std_occupancy, p.std_occupancy) END, 4) AS std_occupancy
        FROM updated_meters m
        CROSS JOIN (SELECT DISTINCT dow, bucket FROM pole_profiles) b
        LEFT JOIN pole_profiles p ON m.pole_id = p.pole_id AND b.dow = p.dow AND b.bucket = p.bucket
        LEFT JOIN sub_area_profiles s ON m.sub_area = s.sub_area AND b.dow = s.dow AND b.bucket = s.bucket
    """)

    con.execute(f"COPY final_profiles TO '{PROFILES_PARQUET.as_posix()}' (FORMAT PARQUET)")
    total_profiles = con.execute("SELECT count(*) FROM final_profiles").fetchone()[0]
    likely_cnt = con.execute("SELECT count(*) FROM final_profiles WHERE band = 'likely'").fetchone()[0]
    possible_cnt = con.execute("SELECT count(*) FROM final_profiles WHERE band = 'possible'").fetchone()[0]
    unlikely_cnt = con.execute("SELECT count(*) FROM final_profiles WHERE band = 'unlikely'").fetchone()[0]

    logger.info(
        f"Availability profiles generated: {total_profiles:,} profile records. "
        f"Bands: {likely_cnt:,} likely ({likely_cnt/total_profiles*100:.1f}%), "
        f"{possible_cnt:,} possible ({possible_cnt/total_profiles*100:.1f}%), "
        f"{unlikely_cnt:,} unlikely ({unlikely_cnt/total_profiles*100:.1f}%)."
    )


if __name__ == "__main__":
    run_profiles()

