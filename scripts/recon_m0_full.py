import duckdb
import datetime

loc_file = "data/raw/locations/parking_meters_current.csv"
trans_file = "data/raw/transactions/treas_parking_payments_2026_datasd.csv"

print("="*70)
print("SAN DIEGO PARKING RECONNAISSANCE - MILESTONE 0")
print("="*70)

# 1. Locations
print("\n--- 1. LOCATIONS SUMMARY ---")
duckdb.sql(f"""
    SELECT 
        count(*) as total_meters,
        count(distinct pole) as distinct_poles,
        count(*) filter (latitude is null or longitude is null) as null_coords,
        count(*) filter (latitude < 32.5 or latitude > 33.3 or longitude < -117.6 or longitude > -116.8) as out_of_bounds_coords,
        count(*) filter (multi_space = 'Y') as multi_space_meters,
        count(*) filter (multi_space is null or multi_space != 'Y') as single_space_meters
    FROM read_csv('{loc_file}')
""").show()

# 2. Transactions
print("\n--- 2. TRANSACTIONS SUMMARY (2026) ---")
duckdb.sql(f"""
    SELECT 
        count(*) as total_transactions,
        count(distinct transaction_id) as distinct_transaction_ids,
        count(distinct pole_id) as distinct_trans_poles,
        min(date_trans_start) as min_trans_start,
        max(date_trans_start) as max_trans_start,
        min(date_meter_expire) as min_meter_expire,
        max(date_meter_expire) as max_meter_expire,
        min(trans_amt) as min_amt,
        max(trans_amt) as max_amt,
        round(avg(trans_amt), 2) as avg_amt,
        round(median(trans_amt), 2) as median_amt
    FROM read_csv('{trans_file}')
""").show()

# 3. Quality & Edge Cases in Transactions
print("\n--- 3. TRANSACTION DATA QUALITY CHECKS ---")
duckdb.sql(f"""
    SELECT 
        count(*) filter (transaction_id is null) as null_tx_ids,
        count(*) filter (pole_id is null) as null_poles,
        count(*) filter (date_meter_expire <= date_trans_start) as expire_before_start,
        count(*) filter (epoch(date_meter_expire::TIMESTAMP) - epoch(date_trans_start::TIMESTAMP) > 12 * 3600) as duration_over_12h,
        count(*) filter (epoch(date_meter_expire::TIMESTAMP) - epoch(date_trans_start::TIMESTAMP) <= 0) as zero_or_negative_duration,
        count(*) filter (trans_amt <= 0) as zero_or_negative_amt
    FROM read_csv('{trans_file}')
""").show()

# 4. Distributions
print("\n--- 4. METER TYPE IN TRANSACTIONS ---")
duckdb.sql(f"""
    SELECT meter_type, count(*) as count, round(count(*) * 100.0 / sum(count(*)) over (), 2) as pct
    FROM read_csv('{trans_file}')
    GROUP BY meter_type
    ORDER BY count DESC
""").show()

print("\n--- 5. PAYMENT METHODS ---")
duckdb.sql(f"""
    SELECT pay_method, count(*) as count, round(count(*) * 100.0 / sum(count(*)) over (), 2) as pct
    FROM read_csv('{trans_file}')
    GROUP BY pay_method
    ORDER BY count DESC
""").show()

# 6. Join Match Rate
print("\n--- 6. JOIN MATCH RATE (POLE VS POLE_ID) ---")
duckdb.sql(f"""
    WITH t_poles AS (
        SELECT DISTINCT pole_id FROM read_csv('{trans_file}') WHERE pole_id IS NOT NULL
    ),
    l_poles AS (
        SELECT DISTINCT pole FROM read_csv('{loc_file}') WHERE pole IS NOT NULL
    )
    SELECT 
        (SELECT count(*) FROM l_poles) as active_meter_poles,
        (SELECT count(*) FROM t_poles) as trans_poles_2026,
        (SELECT count(*) FROM t_poles WHERE pole_id IN (SELECT pole FROM l_poles)) as matched_poles,
        (SELECT count(*) FROM t_poles WHERE pole_id NOT IN (SELECT pole FROM l_poles)) as trans_poles_missing_in_meters,
        round((SELECT count(*) FROM t_poles WHERE pole_id IN (SELECT pole FROM l_poles)) * 100.0 / (SELECT count(*) FROM t_poles), 2) as trans_match_rate_pct,
        round((SELECT count(*) FROM l_poles WHERE pole IN (SELECT pole_id FROM t_poles)) * 100.0 / (SELECT count(*) FROM l_poles), 2) as meters_with_activity_pct
""").show()

# 7. Space Identifiers Check
print("\n--- 7. CHECK FOR SPACE IDENTIFIERS IN POLE_ID OR CONFIG ---")
duckdb.sql(f"""
    SELECT pole_id, count(*) as cnt
    FROM read_csv('{trans_file}')
    WHERE meter_type = 'MS'
    GROUP BY pole_id
    LIMIT 10
""").show()

# 8. Sample transactions with duration
print("\n--- 8. SAMPLE TRANSACTIONS WITH DURATION ---")
duckdb.sql(f"""
    SELECT 
        transaction_id, pole_id, meter_type, pay_method,
        date_trans_start, date_meter_expire,
        round((epoch(date_meter_expire::TIMESTAMP) - epoch(date_trans_start::TIMESTAMP)) / 60.0, 1) as duration_minutes,
        trans_amt
    FROM read_csv('{trans_file}')
    LIMIT 10
""").show()

