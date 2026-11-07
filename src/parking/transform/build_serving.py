"""Serving Database Builder (Stage 6).

Consolidates meters, availability profiles, and pipeline metadata into
data/serving/parking.duckdb using an atomic file swap so the running API
never reads a partially built database.
"""

import logging
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict
import duckdb
import yaml

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

SERVING_DIR = Path("data/serving")
CLEAN_DIR = Path("data/clean")
CONFIG_PATH = Path("config.yaml")

METERS_PARQUET = CLEAN_DIR / "meters.parquet"
PROFILES_PARQUET = CLEAN_DIR / "profiles.parquet"
TRANS_PARQUET = CLEAN_DIR / "transactions.parquet"

SERVING_DB = SERVING_DIR / "parking.duckdb"
SERVING_DB_TMP = SERVING_DIR / "parking.duckdb.tmp"


def build_serving_db():
    SERVING_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    if SERVING_DB_TMP.exists():
        SERVING_DB_TMP.unlink()

    logger.info(f"Building serving database at {SERVING_DB_TMP}...")
    con = duckdb.connect(SERVING_DB_TMP.as_posix())

    # 1. Meters table
    logger.info("Writing meters table...")
    con.execute(f"""
        CREATE TABLE meters AS
        SELECT 
            pole_id,
            lat,
            lon,
            zone,
            area,
            sub_area,
            meter_type,
            rate_per_hour,
            max_minutes,
            enforced_hours_json,
            status
        FROM read_parquet('{METERS_PARQUET.as_posix()}')
    """)

    # 2. Availability profiles table
    logger.info("Writing availability_profile table...")
    con.execute(f"""
        CREATE TABLE availability_profile AS
        SELECT 
            pole_id,
            dow,
            bucket,
            p_available,
            band,
            confidence,
            basis,
            n_dates,
            mean_occupancy,
            std_occupancy
        FROM read_parquet('{PROFILES_PARQUET.as_posix()}')
    """)

    # 3. Metadata table
    logger.info("Computing metadata...")
    data_as_of = con.execute(f"SELECT max(trans_start) FROM read_parquet('{TRANS_PARQUET.as_posix()}')").fetchone()[0]
    built_at = datetime.now(timezone.utc).isoformat()
    min_date = data_as_of.date() - __import__("datetime").timedelta(weeks=cfg.get("profile_window_weeks", 26))

    con.execute(f"""
        CREATE TABLE metadata AS
        SELECT 
            '{data_as_of.isoformat()}' AS data_as_of,
            '{built_at}' AS built_at,
            '{min_date.isoformat()}' AS profile_window_start,
            '{data_as_of.date().isoformat()}' AS profile_window_end,
            '1.0.0' AS pipeline_version,
            (SELECT count(*) FROM meters) AS meter_count,
            (SELECT count(*) FROM meters WHERE status = 'active') AS active_meter_count
    """)

    # Create serving indexes for fast lookups
    con.execute("CREATE INDEX idx_profile_lookup ON availability_profile(pole_id, dow, bucket)")
    con.execute("CREATE INDEX idx_meters_pole ON meters(pole_id)")

    con.close()

    # 4. Atomic file rename
    if SERVING_DB.exists():
        SERVING_DB.unlink()
    shutil.move(SERVING_DB_TMP, SERVING_DB)
    logger.info(f"Serving database atomically swapped into place at {SERVING_DB} ({SERVING_DB.stat().st_size / (1024*1024):.1f} MB).")


if __name__ == "__main__":
    build_serving_db()

