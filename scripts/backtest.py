"""Backtesting Script (Milestone 2).

Evaluates availability profiles against a 4-week holdout dataset.
Compares model MAE (Mean Absolute Error) against a naive baseline
(each pole's overall mean occupancy).
Generates reports/backtest_<date>.md.
"""

import logging
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import duckdb
import yaml
from parking.transform.profiles import get_california_holidays

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

CLEAN_DIR = Path("data/clean")
REPORTS_DIR = Path("reports")
CONFIG_PATH = Path("config.yaml")

OCCUPANCY_PARQUET = CLEAN_DIR / "occupancy.parquet"
METERS_PARQUET = CLEAN_DIR / "meters.parquet"


def run_backtest():
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    profile_weeks = cfg.get("profile_window_weeks", 26)
    half_life_weeks = cfg.get("recency_half_life_weeks", 8)
    half_life_days = half_life_weeks * 7.0
    decay_lambda = math.log(2) / half_life_days

    con = duckdb.connect()

    # 1. Determine timeline
    max_ts = con.execute(f"SELECT max(bucket_start) FROM read_parquet('{OCCUPANCY_PARQUET.as_posix()}')").fetchone()[0]
    holdout_start = max_ts - timedelta(weeks=4)
    train_start = holdout_start - timedelta(weeks=profile_weeks - 4)

    logger.info(f"Backtesting: Train window [{train_start.date()} to {holdout_start.date()}], Holdout [{holdout_start.date()} to {max_ts.date()}]...")

    # 2. Exclude holidays
    holidays_set = get_california_holidays(train_start.year, max_ts.year)
    holidays_sql_list = ", ".join(f"'{h}'::DATE" for h in sorted(holidays_set))
    con.execute(f"""
        CREATE TEMP TABLE holiday_dates AS
        SELECT unnest([{holidays_sql_list}]) AS holiday_date
    """)

    # 3. Train profiles on training period only
    con.execute(f"""
        CREATE TEMP TABLE train_grid AS
        SELECT 
            o.pole_id,
            m.sub_area,
            dayofweek(o.bucket_start) AS dow,
            (hour(o.bucket_start) * 4 + minute(o.bucket_start) / 15)::INTEGER AS bucket,
            o.occupied_fraction,
            exp(-{decay_lambda} * date_diff('day', o.bucket_start::DATE, '{holdout_start.date()}'::DATE)) AS weight
        FROM read_parquet('{OCCUPANCY_PARQUET.as_posix()}') o
        INNER JOIN read_parquet('{METERS_PARQUET.as_posix()}') m ON o.pole_id = m.pole_id
        LEFT JOIN holiday_dates h ON o.bucket_start::DATE = h.holiday_date
        WHERE o.bucket_start >= '{train_start}' AND o.bucket_start < '{holdout_start}'
          AND h.holiday_date IS NULL
    """)

    # Model predictions: (pole_id, dow, bucket)
    con.execute("""
        CREATE TEMP TABLE model_preds AS
        SELECT 
            pole_id, dow, bucket,
            SUM(weight * occupied_fraction) / SUM(weight) AS pred_occupancy
        FROM train_grid
        GROUP BY pole_id, dow, bucket
    """)

    # Naive baseline: overall pole mean occupancy across all times
    con.execute("""
        CREATE TEMP TABLE naive_baseline AS
        SELECT 
            pole_id,
            AVG(occupied_fraction) AS naive_occupancy
        FROM train_grid
        GROUP BY pole_id
    """)

    # 4. Holdout evaluation dataset
    con.execute(f"""
        CREATE TEMP TABLE holdout_data AS
        SELECT 
            o.pole_id,
            dayofweek(o.bucket_start) AS dow,
            (hour(o.bucket_start) * 4 + minute(o.bucket_start) / 15)::INTEGER AS bucket,
            o.occupied_fraction AS actual_occupancy
        FROM read_parquet('{OCCUPANCY_PARQUET.as_posix()}') o
        LEFT JOIN holiday_dates h ON o.bucket_start::DATE = h.holiday_date
        WHERE o.bucket_start >= '{holdout_start}' AND o.bucket_start <= '{max_ts}'
          AND h.holiday_date IS NULL
    """)

    # 5. Evaluate MAE
    eval_stats = con.execute("""
        SELECT 
            COUNT(*) AS total_points,
            ROUND(AVG(ABS(h.actual_occupancy - m.pred_occupancy)), 4) AS model_mae,
            ROUND(AVG(ABS(h.actual_occupancy - n.naive_occupancy)), 4) AS naive_mae,
            ROUND(AVG(POW(h.actual_occupancy - m.pred_occupancy, 2)), 4) AS model_mse,
            ROUND(AVG(POW(h.actual_occupancy - n.naive_occupancy, 2)), 4) AS naive_mse
        FROM holdout_data h
        JOIN model_preds m ON h.pole_id = m.pole_id AND h.dow = m.dow AND h.bucket = m.bucket
        JOIN naive_baseline n ON h.pole_id = n.pole_id
    """).fetchone()

    total_pts, model_mae, naive_mae, model_mse, naive_mse = eval_stats
    mae_improvement = ((naive_mae - model_mae) / naive_mae) * 100.0

    logger.info(f"Evaluation complete on {total_pts:,} holdout points.")
    logger.info(f"Model MAE: {model_mae} vs. Naive Baseline MAE: {naive_mae} ({mae_improvement:.1f}% improvement).")

    # Calibration table by predicted availability band
    calib_rows = con.execute("""
        WITH scored AS (
            SELECT 
                h.actual_occupancy,
                m.pred_occupancy,
                (1.0 - m.pred_occupancy) AS p_available,
                CASE 
                    WHEN (1.0 - m.pred_occupancy) >= 0.6 THEN 'likely'
                    WHEN (1.0 - m.pred_occupancy) >= 0.3 THEN 'possible'
                    ELSE 'unlikely'
                END AS band
            FROM holdout_data h
            JOIN model_preds m ON h.pole_id = m.pole_id AND h.dow = m.dow AND h.bucket = m.bucket
        )
        SELECT 
            band,
            COUNT(*) AS n_buckets,
            ROUND(AVG(p_available), 3) AS avg_predicted_available,
            ROUND(1.0 - AVG(actual_occupancy), 3) AS actual_unoccupied_fraction,
            ROUND(AVG(ABS(actual_occupancy - pred_occupancy)), 4) AS band_mae
        FROM scored
        GROUP BY band
        ORDER BY avg_predicted_available DESC
    """).fetchall()

    today_str = datetime.now().strftime("%Y-%m-%d")
    report_path = REPORTS_DIR / f"backtest_{today_str}.md"

    calib_md = "\n".join(
        f"| {r[0]} | {r[1]:,} | {r[2]} | {r[3]} | {r[4]} |" for r in calib_rows
    )

    content = f"""# Backtest Evaluation Report — {today_str}

## 1. Evaluation Methodology
- **Train Period**: `{train_start.date()}` to `{holdout_start.date()}` (22 weeks, excluding holidays).
- **Holdout Test Period**: `{holdout_start.date()}` to `{max_ts.date()}` (4 weeks, excluding holidays).
- **Evaluation Metric**: Mean Absolute Error (MAE) and Mean Squared Error (MSE) against true paid occupancy.
- **Validation Scope**: Validates against historical *paid* occupancy only.

## 2. Model Performance vs. Naive Baseline
| Metric | Our Time-Decayed Weekly Model | Naive Baseline (Pole Overall Mean) | Improvement |
|---|---|---|---|
| **MAE** | **{model_mae}** | {naive_mae} | **{mae_improvement:.2f}%** |
| **MSE** | **{model_mse}** | {naive_mse} | **{((naive_mse - model_mse)/naive_mse)*100:.2f}%** |

**Conclusion**: The model outperforms the naive baseline by **{mae_improvement:.1f}%**, confirming strong temporal signal across days of week and time-of-day buckets.

## 3. Calibration Table by Availability Band
| Availability Band | Evaluated Buckets | Mean Predicted Availability ($p$) | Actual Observed Unoccupied Fraction | MAE |
|---|---|---|---|---|
{calib_md}
"""

    with open(report_path, "w", encoding="utf-8") as f:
        f.write(content)
    logger.info(f"Backtest report saved to {report_path}")

    assert model_mae < naive_mae, f"Model MAE ({model_mae}) did not beat naive baseline ({naive_mae})"
    return eval_stats


if __name__ == "__main__":
    run_backtest()
