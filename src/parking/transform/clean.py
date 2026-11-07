"""Data Cleaning Module (Stage 2).

Cleans meter locations and transactions datasets, validates coordinates and durations,
parses meter configuration, separates orphaned transactions into a quarantine table,
and generates a markdown Data Quality report (reports/dq_<date>.md).
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
import duckdb
import yaml

from src.parking.transform.config_parser import (
    normalize_meter_type,
    parse_enforced_hours,
    parse_max_minutes,
    parse_rate_per_hour,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

RAW_DIR = Path("data/raw")
CLEAN_DIR = Path("data/clean")
REPORTS_DIR = Path("reports")
CONFIG_PATH = Path("config.yaml")

LOC_CSV = RAW_DIR / "locations" / "parking_meters_current.csv"
TRANS_CSV = RAW_DIR / "transactions" / "treas_parking_payments_2026_datasd.csv"

METERS_PARQUET = CLEAN_DIR / "meters.parquet"
TRANS_PARQUET = CLEAN_DIR / "transactions.parquet"
QUARANTINE_PARQUET = CLEAN_DIR / "quarantine_transactions.parquet"


def load_config() -> Dict[str, Any]:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def clean_meters(con: duckdb.DuckDBPyConnection, cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Clean meters dataset, validate coordinates, parse configs."""
    logger.info("Cleaning meters locations...")
    min_lat = cfg.get("bounds", {}).get("min_lat", 32.5)
    max_lat = cfg.get("bounds", {}).get("max_lat", 33.3)
    min_lon = cfg.get("bounds", {}).get("min_lon", -117.6)
    max_lon = cfg.get("bounds", {}).get("max_lon", -116.8)

    raw_meters = con.execute(f"SELECT * FROM read_csv('{LOC_CSV.as_posix()}')").fetchall()
    cols = [col[0] for col in con.description]
    col_idx = {name: i for i, name in enumerate(cols)}

    total_meters = len(raw_meters)
    valid_meters = []
    dropped_coords = 0
    dropped_spares = 0

    for row in raw_meters:
        pole = str(row[col_idx["pole"]]).strip() if row[col_idx["pole"]] is not None else ""
        zone = str(row[col_idx["zone"]]).strip() if row[col_idx["zone"]] is not None else ""
        area = str(row[col_idx["area"]]).strip() if row[col_idx["area"]] is not None else ""
        sub_area_val = row[col_idx.get("sub-area", col_idx.get("sub_area", -1))]
        sub_area = str(sub_area_val).strip() if sub_area_val is not None else ""

        lat_val = row[col_idx["latitude"]]
        lon_val = row[col_idx["longitude"]]

        # Coordinate check
        try:
            lat = float(lat_val)
            lon = float(lon_val)
        except (ValueError, TypeError):
            dropped_coords += 1
            continue

        if lat < min_lat or lat > max_lat or lon < min_lon or lon > max_lon:
            dropped_coords += 1
            continue

        if zone.lower() == "spares" or "spares" in pole.lower():
            dropped_spares += 1
            continue

        price_val = row[col_idx.get("price")]
        time_limit_val = row[col_idx.get("time_limit")]
        configname_val = row[col_idx.get("configname")]
        time_start_val = row[col_idx.get("time_start")]
        time_end_val = row[col_idx.get("time_end")]
        days_op_val = row[col_idx.get("days_in_operation")]
        multi_space_val = row[col_idx.get("multi_space")]

        price_str = str(price_val) if price_val is not None else None
        time_limit_str = str(time_limit_val) if time_limit_val is not None else None
        configname_str = str(configname_val) if configname_val is not None else None
        time_start_str = str(time_start_val) if time_start_val is not None else None
        time_end_str = str(time_end_val) if time_end_val is not None else None
        days_op_str = str(days_op_val) if days_op_val is not None else None
        multi_space_str = str(multi_space_val) if multi_space_val is not None else None

        rate_per_hour = parse_rate_per_hour(price_str, configname_str)
        max_minutes = parse_max_minutes(time_limit_str, configname_str)
        meter_type = normalize_meter_type(multi_space_str, configname_str)
        enforced_dict = parse_enforced_hours(time_start_str, time_end_str, days_op_str, configname_str)
        enforced_hours_json = json.dumps(enforced_dict) if enforced_dict else None

        valid_meters.append((
            pole,
            lat,
            lon,
            zone,
            area,
            sub_area,
            meter_type,
            rate_per_hour,
            max_minutes,
            enforced_hours_json,
            "active"
        ))

    con.execute("DROP TABLE IF EXISTS clean_meters")
    con.execute("""
        CREATE TABLE clean_meters (
            pole_id VARCHAR PRIMARY KEY,
            lat DOUBLE,
            lon DOUBLE,
            zone VARCHAR,
            area VARCHAR,
            sub_area VARCHAR,
            meter_type VARCHAR,
            rate_per_hour DOUBLE,
            max_minutes INTEGER,
            enforced_hours_json VARCHAR,
            status VARCHAR
        )
    """)
    con.executemany("INSERT INTO clean_meters VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", valid_meters)
    con.execute(f"COPY clean_meters TO '{METERS_PARQUET.as_posix()}' (FORMAT PARQUET)")

    logger.info(f"Clean meters written: {len(valid_meters):,} poles. Dropped: {dropped_coords} invalid coords, {dropped_spares} spares.")
    return {
        "total_raw": total_meters,
        "clean_count": len(valid_meters),
        "dropped_coords": dropped_coords,
        "dropped_spares": dropped_spares,
    }


def clean_transactions(con: duckdb.DuckDBPyConnection, cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Clean transactions, deduplicate, filter invalid intervals, and quarantine unmatched poles."""
    logger.info("Cleaning transactions dataset...")
    max_session_hours = cfg.get("max_session_hours", 12)
    max_duration_seconds = max_session_hours * 3600

    # 1. Total raw transactions
    raw_count = con.execute(f"SELECT count(*) FROM read_csv('{TRANS_CSV.as_posix()}')").fetchone()[0]

    # 2. Extract valid transactions joining with clean_meters
    con.execute("DROP TABLE IF EXISTS raw_tx")
    con.execute(f"""
        CREATE TEMP TABLE raw_tx AS
        SELECT 
            transaction_id::VARCHAR as uuid,
            pole_id::VARCHAR as pole_id,
            CASE WHEN meter_type = 'MS' THEN 'multi_space' ELSE 'single_space' END as meter_type,
            date_trans_start::TIMESTAMP as trans_start,
            date_meter_expire::TIMESTAMP as meter_expire,
            epoch(date_meter_expire::TIMESTAMP) - epoch(date_trans_start::TIMESTAMP) as duration_seconds,
            trans_amt::DOUBLE as trans_amt,
            pay_method::VARCHAR as pay_method
        FROM read_csv('{TRANS_CSV.as_posix()}')
        WHERE transaction_id IS NOT NULL
    """)

    # Metric counts
    total_loaded = con.execute("SELECT count(*) FROM raw_tx").fetchone()[0]
    expire_before_start = con.execute("SELECT count(*) FROM raw_tx WHERE duration_seconds <= 0").fetchone()[0]
    exceeds_max_hours = con.execute(f"SELECT count(*) FROM raw_tx WHERE duration_seconds > {max_duration_seconds}").fetchone()[0]
    null_poles = con.execute("SELECT count(*) FROM raw_tx WHERE pole_id IS NULL OR pole_id = ''").fetchone()[0]

    # Filtered valid intervals
    con.execute(f"""
        CREATE TEMP TABLE filtered_tx AS
        SELECT DISTINCT ON (uuid) *
        FROM raw_tx
        WHERE duration_seconds > 0 
          AND duration_seconds <= {max_duration_seconds}
          AND pole_id IS NOT NULL 
          AND pole_id != ''
    """)

    # Split into clean (matched to clean_meters) and quarantined
    con.execute("DROP TABLE IF EXISTS clean_tx")
    con.execute(f"""
        CREATE TABLE clean_tx AS
        SELECT f.*
        FROM filtered_tx f
        INNER JOIN clean_meters m ON f.pole_id = m.pole_id
    """)
    con.execute(f"COPY clean_tx TO '{TRANS_PARQUET.as_posix()}' (FORMAT PARQUET)")
    clean_count = con.execute("SELECT count(*) FROM clean_tx").fetchone()[0]

    con.execute("DROP TABLE IF EXISTS quarantine_tx")
    con.execute(f"""
        CREATE TABLE quarantine_tx AS
        SELECT f.*
        FROM filtered_tx f
        LEFT JOIN clean_meters m ON f.pole_id = m.pole_id
        WHERE m.pole_id IS NULL
    """)
    con.execute(f"COPY quarantine_tx TO '{QUARANTINE_PARQUET.as_posix()}' (FORMAT PARQUET)")
    quarantine_count = con.execute("SELECT count(*) FROM quarantine_tx").fetchone()[0]
    quarantine_poles = con.execute("SELECT count(distinct pole_id) FROM quarantine_tx").fetchone()[0]

    logger.info(f"Clean transactions written: {clean_count:,} rows. Quarantined: {quarantine_count:,} rows ({quarantine_poles} poles).")

    return {
        "raw_count": raw_count,
        "total_loaded": total_loaded,
        "expire_before_start": expire_before_start,
        "exceeds_max_hours": exceeds_max_hours,
        "null_poles": null_poles,
        "clean_count": clean_count,
        "quarantine_count": quarantine_count,
        "quarantine_poles": quarantine_poles,
    }


def generate_dq_report(meters_stats: Dict[str, Any], trans_stats: Dict[str, Any], con: duckdb.DuckDBPyConnection):
    """Generate Data Quality report markdown artifact."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    today_str = datetime.now().strftime("%Y-%m-%d")
    report_path = REPORTS_DIR / f"dq_{today_str}.md"

    # Coverage by area
    area_coverage = con.execute("""
        SELECT 
            m.area,
            count(distinct m.pole_id) as total_meters,
            count(distinct t.pole_id) as active_meters,
            round(count(distinct t.pole_id) * 100.0 / count(distinct m.pole_id), 1) as activity_pct,
            count(t.uuid) as total_payments
        FROM clean_meters m
        LEFT JOIN clean_tx t ON m.pole_id = t.pole_id
        GROUP BY m.area
        ORDER BY total_payments DESC
    """).fetchall()

    area_table_rows = "\n".join(
        f"| {r[0]} | {r[1]} | {r[2]} | {r[3]}% | {r[4]:,} |" for r in area_coverage
    )

    report_content = f"""# Data Quality Report — {today_str}

## 1. Meter Locations Cleaning Summary
- **Raw meters extracted**: {meters_stats['total_raw']:,}
- **Dropped out-of-bounds coordinates**: {meters_stats['dropped_coords']}
- **Dropped test/spare meters**: {meters_stats['dropped_spares']}
- **Final clean meters**: {meters_stats['clean_count']:,}

## 2. Transactions Cleaning Summary
- **Raw transactions (2026)**: {trans_stats['raw_count']:,}
- **Dropped invalid duration (`expire <= start`)**: {trans_stats['expire_before_start']:,} ({trans_stats['expire_before_start']/trans_stats['raw_count']*100:.2f}%)
- **Dropped duration > 12 hours**: {trans_stats['exceeds_max_hours']:,} ({trans_stats['exceeds_max_hours']/trans_stats['raw_count']*100:.2f}%)
- **Dropped null `pole_id`**: {trans_stats['null_poles']}
- **Clean matched transactions**: {trans_stats['clean_count']:,} ({trans_stats['clean_count']/trans_stats['raw_count']*100:.2f}%)
- **Quarantined transactions (unmatched poles)**: {trans_stats['quarantine_count']:,} ({trans_stats['quarantine_poles']} distinct poles)

## 3. Geographic Coverage by Area
| Neighborhood / Area | Total Meters | Active Meters | Coverage % | Total Transactions |
|---|---|---|---|---|
{area_table_rows}

## 4. Pipeline Artifacts Emitted
- `data/clean/meters.parquet`
- `data/clean/transactions.parquet`
- `data/clean/quarantine_transactions.parquet`
"""
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_content)
    logger.info(f"Data quality report saved to {report_path}")


def run_clean():
    CLEAN_DIR.mkdir(parents=True, exist_ok=True)
    cfg = load_config()
    con = duckdb.connect()

    meters_stats = clean_meters(con, cfg)
    trans_stats = clean_transactions(con, cfg)
    generate_dq_report(meters_stats, trans_stats, con)


if __name__ == "__main__":
    run_clean()

