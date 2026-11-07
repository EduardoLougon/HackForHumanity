"""Unit tests for occupancy grid bucket math."""

import duckdb
import pytest


def test_bucket_math_overlap():
    """A session 10:05-10:35 yields:
    - 10:00 bucket (10:00-10:15): 10 min overlap / 15 min = 0.667
    - 10:15 bucket (10:15-10:30): 15 min overlap / 15 min = 1.000
    - 10:30 bucket (10:30-10:45): 5 min overlap / 15 min = 0.333
    """
    con = duckdb.connect()
    con.execute("""
        CREATE TEMP TABLE session (
            pole_id VARCHAR,
            session_start TIMESTAMP,
            session_end TIMESTAMP
        )
    """)
    con.execute("INSERT INTO session VALUES ('P1', '2026-06-01 10:05:00', '2026-06-01 10:35:00')")

    con.execute("""
        CREATE TEMP TABLE bucket_overlaps AS
        WITH exploded AS (
            SELECT 
                pole_id,
                session_start,
                session_end,
                unnest(generate_series(
                    time_bucket(INTERVAL 15 MINUTE, session_start),
                    session_end - INTERVAL 1 MICROSECOND,
                    INTERVAL 15 MINUTE
                )) AS bucket_start
            FROM session
        )
        SELECT 
            bucket_start,
            ROUND(SUM(date_diff('second',
                GREATEST(session_start, bucket_start),
                LEAST(session_end, bucket_start + INTERVAL 15 MINUTE)
            )) / 900.0, 3) AS occupied_fraction
        FROM exploded
        GROUP BY bucket_start
        ORDER BY bucket_start
    """)

    rows = con.execute("SELECT strftime(bucket_start, '%H:%M'), occupied_fraction FROM bucket_overlaps").fetchall()
    assert len(rows) == 3
    assert rows[0] == ("10:00", 0.667)
    assert rows[1] == ("10:15", 1.000)
    assert rows[2] == ("10:30", 0.333)


def test_zero_filled_grid():
    """Verify that a pole with 1 session in a 10-bucket period has ~10% occupancy, NOT 100%."""
    con = duckdb.connect()
    con.execute("""
        CREATE TEMP TABLE grid (
            pole_id VARCHAR,
            bucket_start TIMESTAMP
        )
    """)
    # 10 buckets
    con.execute("""
        INSERT INTO grid
        SELECT 'P1', unnest(generate_series(
            TIMESTAMP '2026-06-01 10:00:00',
            TIMESTAMP '2026-06-01 12:15:00',
            INTERVAL 15 MINUTE
        ))
    """)

    # 1 fully occupied session in 1 bucket
    con.execute("""
        CREATE TEMP TABLE session_overlaps (
            pole_id VARCHAR,
            bucket_start TIMESTAMP,
            overlap_seconds DOUBLE
        )
    """)
    con.execute("INSERT INTO session_overlaps VALUES ('P1', '2026-06-01 10:00:00', 900.0)")

    # Join and fill missing with 0
    res = con.execute("""
        SELECT AVG(COALESCE(o.overlap_seconds, 0) / 900.0)
        FROM grid g
        LEFT JOIN session_overlaps o ON g.pole_id = o.pole_id AND g.bucket_start = o.bucket_start
    """).fetchone()[0]

    assert res == pytest.approx(0.10, abs=0.01)
