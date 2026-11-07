"""Occupancy Grid Module (Stage 4).

Discretizes each enforced day into 15-minute buckets.
Computes occupied fraction per (pole, bucket_start) for both single-space and multi-space meters.
CRITICAL: Builds a full zero-filled grid so buckets with 0 sessions have 0 occupancy.
Outputs: data/clean/occupancy.parquet
"""

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple
import duckdb
import yaml

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

CLEAN_DIR = Path("data/clean")
CONFIG_PATH = Path("config.yaml")

METERS_PARQUET = CLEAN_DIR / "meters.parquet"
SESSIONS_PARQUET = CLEAN_DIR / "sessions.parquet"
OCCUPANCY_PARQUET = CLEAN_DIR / "occupancy.parquet"


def load_config() -> Dict[str, Any]:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def run_occupancy():
    CLEAN_DIR.mkdir(parents=True, exist_ok=True)
    cfg = load_config()
    bucket_minutes = cfg.get("bucket_minutes", 15)
    bucket_seconds = bucket_minutes * 60.0
    capacity_pct = cfg.get("capacity_percentile", 95) / 100.0
    profile_weeks = cfg.get("profile_window_weeks", 26)

    con = duckdb.connect()

    # Find the max date in sessions to establish the window
    max_ts = con.execute(f"SELECT max(session_start) FROM read_parquet('{SESSIONS_PARQUET.as_posix()}')").fetchone()[0]
    min_window_ts = max_ts - timedelta(weeks=profile_weeks)
    logger.info(f"Computing occupancy grid from {min_window_ts} to {max_ts} ({profile_weeks} weeks)...")

    # 1. Filter sessions to the profile window
    con.execute(f"""
        CREATE TEMP TABLE window_sessions AS
        SELECT *
        FROM read_parquet('{SESSIONS_PARQUET.as_posix()}')
        WHERE session_start >= '{min_window_ts}' AND session_start <= '{max_ts}'
    """)

    # 2. Estimate capacity for multi-space poles (p95 of concurrent sessions)
    # Concurrent sessions sampled at bucket starts
    logger.info("Estimating multi-space capacities...")
    con.execute(f"""
        CREATE TEMP TABLE ms_concurrent AS
        WITH exploded_ms AS (
            SELECT 
                s.pole_id,
                unnest(generate_series(
                    time_bucket(INTERVAL '{bucket_minutes} MINUTE', s.session_start),
                    s.session_end - INTERVAL 1 MICROSECOND,
                    INTERVAL '{bucket_minutes} MINUTE'
                )) AS bucket_start
            FROM window_sessions s
            INNER JOIN read_parquet('{METERS_PARQUET.as_posix()}') m ON s.pole_id = m.pole_id
            WHERE m.meter_type = 'multi_space'
        )
        SELECT pole_id, bucket_start, count(*) AS concurrent_count
        FROM exploded_ms
        GROUP BY pole_id, bucket_start
    """)

    con.execute(f"""
        CREATE TEMP TABLE ms_capacity AS
        SELECT 
            pole_id,
            GREATEST(1, ROUND(quantile_cont(concurrent_count, {capacity_pct})))::INTEGER AS capacity_est
        FROM ms_concurrent
        GROUP BY pole_id
    """)

    # 3. Compute session overlaps per (pole_id, bucket_start)
    logger.info("Computing session overlaps per 15-minute bucket...")
    con.execute(f"""
        CREATE TEMP TABLE session_overlaps AS
        WITH exploded AS (
            SELECT 
                s.pole_id,
                s.session_start,
                s.session_end,
                unnest(generate_series(
                    time_bucket(INTERVAL '{bucket_minutes} MINUTE', s.session_start),
                    s.session_end - INTERVAL 1 MICROSECOND,
                    INTERVAL '{bucket_minutes} MINUTE'
                )) AS bucket_start
            FROM window_sessions s
        )
        SELECT 
            pole_id,
            bucket_start,
            SUM(date_diff('second',
                GREATEST(session_start, bucket_start),
                LEAST(session_end, bucket_start + INTERVAL '{bucket_minutes} MINUTE')
            )) AS overlap_seconds
        FROM exploded
        GROUP BY pole_id, bucket_start
    """)

    # 4. Generate the complete enforced calendar grid (pole × date × bucket)
    # Default enforcement: 8:00 AM to 8:00 PM Mon-Sat if config is missing
    logger.info("Building full enforced grid to capture zero-session intervals...")
    con.execute(f"""
        CREATE TEMP TABLE dates AS
        SELECT unnest(generate_series(
            '{min_window_ts.date()}'::DATE,
            '{max_ts.date()}'::DATE,
            INTERVAL 1 DAY
        ))::DATE AS dt
    """)

    # 15-minute time buckets between 08:00 and 20:00 (48 buckets per enforced day)
    con.execute(f"""
        CREATE TEMP TABLE time_buckets AS
        SELECT CAST(unnest(generate_series(
            TIMESTAMP '2000-01-01 08:00:00',
            TIMESTAMP '2000-01-01 19:45:00',
            INTERVAL '{bucket_minutes} MINUTE'
        )) AS TIME) AS bucket_time
    """)

    # Active poles grid: Monday through Saturday (dayofweek 1 to 6 in DuckDB: Sun=0, Mon=1... Sat=6)
    con.execute("""
        CREATE TEMP TABLE full_grid AS
        SELECT 
            m.pole_id,
            m.meter_type,
            (d.dt + b.bucket_time)::TIMESTAMP AS bucket_start
        FROM read_parquet('data/clean/meters.parquet') m
        CROSS JOIN dates d
        CROSS JOIN time_buckets b
        WHERE dayofweek(d.dt) BETWEEN 1 AND 6
    """)

    # 5. Join overlaps to the full grid, filling missing with 0, and compute occupied_fraction
    logger.info("Calculating final occupied_fraction across full grid...")
    con.execute(f"""
        CREATE TEMP TABLE final_occupancy AS
        SELECT 
            g.pole_id,
            g.bucket_start,
            CASE 
                WHEN g.meter_type = 'single_space' THEN
                    LEAST(1.0, COALESCE(o.overlap_seconds, 0) / {bucket_seconds})
                ELSE
                    LEAST(1.0, (COALESCE(o.overlap_seconds, 0) / {bucket_seconds}) / COALESCE(c.capacity_est, 1))
            END AS occupied_fraction
        FROM full_grid g
        LEFT JOIN session_overlaps o ON g.pole_id = o.pole_id AND g.bucket_start = o.bucket_start
        LEFT JOIN ms_capacity c ON g.pole_id = c.pole_id
    """)

    con.execute(f"COPY final_occupancy TO '{OCCUPANCY_PARQUET.as_posix()}' (FORMAT PARQUET)")
    total_grid_rows = con.execute("SELECT count(*) FROM final_occupancy").fetchone()[0]
    avg_occ = con.execute("SELECT avg(occupied_fraction) FROM final_occupancy").fetchone()[0]
    zero_buckets = con.execute("SELECT count(*) FROM final_occupancy WHERE occupied_fraction = 0").fetchone()[0]

    logger.info(
        f"Occupancy grid generated: {total_grid_rows:,} (pole, bucket) points. "
        f"Mean occupancy: {avg_occ*100:.2f}%. "
        f"Zero-session buckets: {zero_buckets:,} ({zero_buckets/total_grid_rows*100:.1f}%)."
    )


if __name__ == "__main__":
    run_occupancy()
