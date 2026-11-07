import duckdb

csv_path = "data/raw/locations/parking_meters_current.csv"

print("--- 1. BASIC STATS ---")
total_rows = duckdb.sql(f"SELECT count(*) FROM read_csv('{csv_path}')").fetchall()[0][0]
distinct_poles = duckdb.sql(f"SELECT count(distinct pole) FROM read_csv('{csv_path}')").fetchall()[0][0]
print(f"Total rows: {total_rows}, Distinct poles: {distinct_poles}")

print("\n--- 2. COLUMNS & TYPES ---")
duckdb.sql(f"DESCRIBE SELECT * FROM read_csv('{csv_path}')").show()

print("\n--- 3. SAMPLE DATA ---")
duckdb.sql(f"""
    SELECT pole, zone, area, "sub-area" as sub_area, latitude, longitude,
           configname, time_start, time_end, time_limit, days_in_operation,
           price, mobile_pay, multi_space, restrictions
    FROM read_csv('{csv_path}')
    LIMIT 5
""").show()

print("\n--- 4. COORDINATE VALIDATION ---")
duckdb.sql(f"""
    SELECT 
        min(latitude) as min_lat, max(latitude) as max_lat,
        min(longitude) as min_lon, max(longitude) as max_lon,
        count(*) filter (latitude is null or longitude is null) as null_coords,
        count(*) filter (latitude = 0 or longitude = 0) as zero_coords,
        count(*) filter (latitude < 32.5 or latitude > 33.3 or longitude < -117.6 or longitude > -116.8) as out_of_bounds
    FROM read_csv('{csv_path}')
""").show()

print("\n--- 5. SPARES & ANOMALIES ---")
duckdb.sql(f"""
    SELECT zone, count(*) as cnt
    FROM read_csv('{csv_path}')
    WHERE zone = 'Spares' OR pole LIKE '%Spares%'
    GROUP BY zone
""").show()

print("\n--- 6. FIELD VALUE DISTRIBUTIONS ---")
for col in ['zone', 'multi_space', 'mobile_pay', 'time_limit', 'price', 'days_in_operation', 'time_start', 'time_end']:
    print(f"\nValue distribution for '{col}':")
    duckdb.sql(f"""
        SELECT "{col}" as val, count(*) as cnt
        FROM read_csv('{csv_path}')
        GROUP BY "{col}"
        ORDER BY cnt DESC
        LIMIT 10
    """).show()

