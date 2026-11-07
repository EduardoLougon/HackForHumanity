"""Sessionization Module (Stage 3).

Merges consecutive single-space transactions on the same pole separated by <= merge_gap_minutes
into continuous sessions (extensions/top-ups). Retains multi-space transactions as separate
concurrent sessions.
Outputs: data/clean/sessions.parquet
"""

import logging
from pathlib import Path
from typing import Any, Dict
import duckdb
import yaml

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

CLEAN_DIR = Path("data/clean")
CONFIG_PATH = Path("config.yaml")

TRANS_PARQUET = CLEAN_DIR / "transactions.parquet"
SESSIONS_PARQUET = CLEAN_DIR / "sessions.parquet"


def load_config() -> Dict[str, Any]:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def run_sessionize():
    CLEAN_DIR.mkdir(parents=True, exist_ok=True)
    cfg = load_config()
    merge_gap_minutes = cfg.get("merge_gap_minutes", 2)

    logger.info(f"Running sessionization with merge_gap_minutes={merge_gap_minutes}...")
    con = duckdb.connect()

    # 1. Single space sessionization using DuckDB windowed island-and-gaps
    con.execute(f"""
        CREATE TEMP TABLE single_space_sessions AS
        WITH with_prev AS (
            SELECT 
                pole_id,
                trans_start,
                meter_expire,
                MAX(meter_expire) OVER (
                    PARTITION BY pole_id 
                    ORDER BY trans_start, meter_expire
                    ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
                ) AS prev_max_expire
            FROM read_parquet('{TRANS_PARQUET.as_posix()}')
            WHERE meter_type = 'single_space'
        ),
        with_is_new AS (
            SELECT 
                pole_id,
                trans_start,
                meter_expire,
                CASE 
                    WHEN prev_max_expire IS NULL THEN 1
                    WHEN trans_start > prev_max_expire + INTERVAL '{merge_gap_minutes}' MINUTE THEN 1
                    ELSE 0
                END AS is_new_session
            FROM with_prev
        ),
        with_session_id AS (
            SELECT 
                pole_id,
                trans_start,
                meter_expire,
                SUM(is_new_session) OVER (
                    PARTITION BY pole_id 
                    ORDER BY trans_start, meter_expire
                    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                ) AS session_id
            FROM with_is_new
        )
        SELECT 
            pole_id,
            MIN(trans_start) AS session_start,
            MAX(meter_expire) AS session_end,
            COUNT(*)::INTEGER AS n_transactions
        FROM with_session_id
        GROUP BY pole_id, session_id
    """)

    # 2. Multi space transactions as individual sessions
    con.execute(f"""
        CREATE TEMP TABLE multi_space_sessions AS
        SELECT 
            pole_id,
            trans_start AS session_start,
            meter_expire AS session_end,
            1::INTEGER AS n_transactions
        FROM read_parquet('{TRANS_PARQUET.as_posix()}')
        WHERE meter_type = 'multi_space'
    """)

    # 3. Combine and save to sessions.parquet
    con.execute(f"""
        COPY (
            SELECT * FROM single_space_sessions
            UNION ALL
            SELECT * FROM multi_space_sessions
        ) TO '{SESSIONS_PARQUET.as_posix()}' (FORMAT PARQUET)
    """)

    ss_count = con.execute("SELECT count(*) FROM single_space_sessions").fetchone()[0]
    ms_count = con.execute("SELECT count(*) FROM multi_space_sessions").fetchone()[0]
    total_sessions = ss_count + ms_count
    orig_tx = con.execute(f"SELECT count(*) FROM read_parquet('{TRANS_PARQUET.as_posix()}')").fetchone()[0]

    logger.info(
        f"Sessionization complete: {orig_tx:,} transactions -> {total_sessions:,} sessions "
        f"({ss_count:,} single-space, {ms_count:,} multi-space). "
        f"Merged {orig_tx - total_sessions:,} extension transactions."
    )


if __name__ == "__main__":
    run_sessionize()

