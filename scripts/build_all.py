"""Orchestration Script (Stage 6 / Ops).

Runs the full end-to-end refresh pipeline:
fetch -> clean -> sessionize -> occupancy -> profiles -> build_serving
Performs data freshness verification and checks alert conditions.
"""

import logging
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Add project root and src to path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from src.parking.ingest.fetch import fetch_all
from src.parking.transform.clean import run_clean
from src.parking.transform.sessionize import run_sessionize
from src.parking.transform.occupancy import run_occupancy
from src.parking.transform.profiles import run_profiles
from src.parking.transform.build_serving import build_serving_db
import duckdb

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("build_all")


def check_freshness(serving_db_path: Path):
    """Alert if data_as_of is older than twice the expected cadence (2 days)."""
    con = duckdb.connect(serving_db_path.as_posix(), read_only=True)
    row = con.execute("SELECT data_as_of FROM metadata LIMIT 1").fetchone()
    con.close()

    if not row or not row[0]:
        logger.warning("[ALERT] No data_as_of metadata found in serving database!")
        return

    data_as_of = datetime.fromisoformat(row[0])
    if data_as_of.tzinfo is None:
        data_as_of = data_as_of.replace(tzinfo=timezone.utc)

    now = datetime.now(timezone.utc)
    age = now - data_as_of

    if age > timedelta(days=2):
        logger.warning(f"[ALERT] Serving data is stale! data_as_of={data_as_of.isoformat()} (age: {age.days} days, {age.seconds//3600} hours). Stated cadence is Daily.")
    else:
        logger.info(f"[HEALTHY] Serving data freshness is optimal: data_as_of={data_as_of.isoformat()} (age: {age.days}d {age.seconds//3600}h).")


def main():
    t0 = time.time()
    logger.info("==================================================")
    logger.info("Starting Full San Diego Parking Data Pipeline")
    logger.info("==================================================")

    # 1. Ingest
    logger.info("\n--- STEP 1: FETCH DATASETS ---")
    fetch_all(current_year_only=True)

    # 2. Clean
    logger.info("\n--- STEP 2: CLEAN DATASETS ---")
    run_clean()

    # 3. Sessionize
    logger.info("\n--- STEP 3: SESSIONIZE ---")
    run_sessionize()

    # 4. Occupancy Grid
    logger.info("\n--- STEP 4: OCCUPANCY GRID ---")
    run_occupancy()

    # 5. Profiles
    logger.info("\n--- STEP 5: PROFILES ---")
    run_profiles()

    # 6. Build Serving DB
    logger.info("\n--- STEP 6: BUILD SERVING DB ---")
    build_serving_db()

    # 7. Freshness check
    check_freshness(PROJECT_ROOT / "data" / "serving" / "parking.duckdb")

    elapsed = time.time() - t0
    logger.info(f"\nPipeline completed successfully in {elapsed:.1f}s ({elapsed/60:.2f} minutes).")


if __name__ == "__main__":
    main()

