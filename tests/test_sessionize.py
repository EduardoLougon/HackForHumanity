"""Unit tests for sessionization logic."""

from datetime import datetime
import duckdb


def test_single_space_extension_merge():
    """Verify that transactions on same pole within 2 min gap are merged into one session."""
    con = duckdb.connect()
    con.execute("""
        CREATE TEMP TABLE tx (
            pole_id VARCHAR,
            trans_start TIMESTAMP,
            meter_expire TIMESTAMP,
            meter_type VARCHAR
        )
    """)
    # 2 transactions on pole P1: first 10:00 to 11:00, second 11:01 to 12:00 (1 min gap -> merged)
    # 1 transaction on pole P1: 13:00 to 14:00 (60 min gap -> separate session)
    con.execute("""
        INSERT INTO tx VALUES 
        ('P1', '2026-06-01 10:00:00', '2026-06-01 11:00:00', 'single_space'),
        ('P1', '2026-06-01 11:01:00', '2026-06-01 12:00:00', 'single_space'),
        ('P1', '2026-06-01 13:00:00', '2026-06-01 14:00:00', 'single_space')
    """)

    con.execute("""
        CREATE TEMP TABLE sessions AS
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
            FROM tx
            WHERE meter_type = 'single_space'
        ),
        with_is_new AS (
            SELECT 
                pole_id,
                trans_start,
                meter_expire,
                CASE 
                    WHEN prev_max_expire IS NULL THEN 1
                    WHEN trans_start > prev_max_expire + INTERVAL 2 MINUTE THEN 1
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
        ORDER BY session_start
    """)

    res = con.execute("SELECT session_start, session_end, n_transactions FROM sessions").fetchall()
    assert len(res) == 2
    # First merged session: 10:00 to 12:00, 2 transactions
    assert str(res[0][0]) == "2026-06-01 10:00:00"
    assert str(res[0][1]) == "2026-06-01 12:00:00"
    assert res[0][2] == 2

    # Second session: 13:00 to 14:00, 1 transaction
    assert str(res[1][0]) == "2026-06-01 13:00:00"
    assert str(res[1][1]) == "2026-06-01 14:00:00"
    assert res[1][2] == 1


def test_multi_space_concurrent_sessions():
    """Verify that multi-space concurrent transactions are NOT merged."""
    con = duckdb.connect()
    con.execute("""
        CREATE TEMP TABLE tx (
            pole_id VARCHAR,
            trans_start TIMESTAMP,
            meter_expire TIMESTAMP,
            meter_type VARCHAR
        )
    """)
    # 2 concurrent transactions on same multi-space pole MS1:
    con.execute("""
        INSERT INTO tx VALUES 
        ('MS1', '2026-06-01 10:00:00', '2026-06-01 11:00:00', 'multi_space'),
        ('MS1', '2026-06-01 10:15:00', '2026-06-01 11:15:00', 'multi_space')
    """)

    con.execute("""
        CREATE TEMP TABLE ms_sessions AS
        SELECT 
            pole_id,
            trans_start AS session_start,
            meter_expire AS session_end,
            1::INTEGER AS n_transactions
        FROM tx
        WHERE meter_type = 'multi_space'
    """)
    res = con.execute("SELECT count(*) FROM ms_sessions").fetchone()[0]
    assert res == 2

