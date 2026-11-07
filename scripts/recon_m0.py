"""Milestone 0: Data Reconnaissance Script for San Diego Parking Finder."""
import os
import sys
import json
import ssl
import certifi
import urllib.request
from pathlib import Path
import duckdb

RAW_DIR = Path("data/raw")
LOC_DIR = RAW_DIR / "locations"
TRANS_DIR = RAW_DIR / "transactions"
DOCS_DIR = Path("docs")

LOC_DICT_URL = "https://seshat.datasd.org/parking_meters_locations/meters_locations_dictionary_datasd.csv"
LOC_DATA_URL = "https://seshat.datasd.org/parking_meters_locations/treas_parking_meters_loc_datasd.csv"
TRANS_DICT_URL = "https://seshat.datasd.org/parking_meters_transactions/meters_transactions_dictionary_datasd.csv"
TRANS_2026_URL = "https://seshat.datasd.org/parking_meters_transactions/treas_parking_payments_2026_datasd.csv"
TRANS_2025_URL = "https://seshat.datasd.org/parking_meters_transactions/treas_parking_payments_2025_datasd.csv"

def download_file(url: str, dest_path: Path):
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    if dest_path.exists() and dest_path.stat().st_size > 0:
        print(f"[CACHE] {dest_path.name} already exists ({dest_path.stat().st_size / 1024:.1f} KB).")
        return dest_path

    print(f"[DOWNLOAD] Fetching {url} -> {dest_path}...")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    ctx = ssl.create_default_context(cafile=certifi.where())
    with urllib.request.urlopen(req, context=ctx) as resp, open(dest_path, "wb") as f:
        while chunk := resp.read(1024 * 1024):
            f.write(chunk)
    print(f"[DONE] Saved {dest_path.name} ({dest_path.stat().st_size / 1024:.1f} KB).")
    return dest_path

def run_recon():
    LOC_DIR.mkdir(parents=True, exist_ok=True)
    TRANS_DIR.mkdir(parents=True, exist_ok=True)
    DOCS_DIR.mkdir(parents=True, exist_ok=True)

    loc_dict_file = download_file(LOC_DICT_URL, LOC_DIR / "meters_locations_dictionary_datasd.csv")
    loc_data_file = download_file(LOC_DATA_URL, LOC_DIR / "treas_parking_meters_loc_datasd.csv")
    trans_dict_file = download_file(TRANS_DICT_URL, TRANS_DIR / "meters_transactions_dictionary_datasd.csv")
    
    # Try 2026 first; check size and rows
    trans_2026_file = download_file(TRANS_2026_URL, TRANS_DIR / "treas_parking_payments_2026_datasd.csv")

    con = duckdb.connect()

    print("\n" + "="*60)
    print("1. LOCATIONS DATA DICTIONARY")
    print("="*60)
    loc_dict_df = con.execute(f"SELECT * FROM read_csv_auto('{loc_dict_file.as_posix()}')").fetchdf()
    print(loc_dict_df.to_string())

    print("\n" + "="*60)
    print("2. TRANSACTIONS DATA DICTIONARY")
    print("="*60)
    trans_dict_df = con.execute(f"SELECT * FROM read_csv_auto('{trans_dict_file.as_posix()}')").fetchdf()
    print(trans_dict_df.to_string())

    print("\n" + "="*60)
    print("3. LOCATIONS TABLE ANALYSIS")
    print("="*60)
    loc_schema = con.execute(f"DESCRIBE SELECT * FROM read_csv_auto('{loc_data_file.as_posix()}')").fetchdf()
    print("Schema:\n", loc_schema.to_string())

    total_locs = con.execute(f"SELECT count(*) FROM read_csv_auto('{loc_data_file.as_posix()}')").fetchone()[0]
    distinct_poles = con.execute(f"SELECT count(distinct pole) FROM read_csv_auto('{loc_data_file.as_posix()}')").fetchone()[0]
    print(f"Total rows: {total_locs}, Distinct poles: {distinct_poles}")

    loc_samples = con.execute(f"SELECT zone, area, sub_area, pole, config_id, config_name, longitude, latitude FROM read_csv_auto('{loc_data_file.as_posix()}') LIMIT 5").fetchdf()
    print("Sample rows:\n", loc_samples.to_string())

    coord_stats = con.execute(f"""
        SELECT 
            min(latitude) as min_lat, max(latitude) as max_lat,
            min(longitude) as min_lon, max(longitude) as max_lon,
            count(*) filter (latitude is null or longitude is null) as null_coords,
            count(*) filter (latitude = 0 or longitude = 0) as zero_coords
        FROM read_csv_auto('{loc_data_file.as_posix()}')
    """).fetchdf()
    print("Coordinate bounds:\n", coord_stats.to_string())

    config_names = con.execute(f"""
        SELECT config_name, count(*) as cnt 
        FROM read_csv_auto('{loc_data_file.as_posix()}')
        GROUP BY config_name
        ORDER BY cnt DESC
        LIMIT 20
    """).fetchdf()
    print("Top config_name patterns:\n", config_names.to_string())

    print("\n" + "="*60)
    print("4. TRANSACTIONS TABLE ANALYSIS (2026)")
    print("="*60)
    trans_schema = con.execute(f"DESCRIBE SELECT * FROM read_csv_auto('{trans_2026_file.as_posix()}')").fetchdf()
    print("Schema:\n", trans_schema.to_string())

    total_trans = con.execute(f"SELECT count(*) FROM read_csv_auto('{trans_2026_file.as_posix()}')").fetchone()[0]
    print(f"Total rows in 2026: {total_trans}")

    if total_trans < 50000:
        print("2026 has relatively few rows (early in year or partial). Also fetching 2025 for robust checks...")
        trans_2025_file = download_file(TRANS_2025_URL, TRANS_DIR / "treas_parking_payments_2025_datasd.csv")
        active_trans_file = trans_2025_file
    else:
        active_trans_file = trans_2026_file

    trans_sample = con.execute(f"SELECT * FROM read_csv_auto('{active_trans_file.as_posix()}') LIMIT 5").fetchdf()
    print(f"Sample rows from {active_trans_file.name}:\n", trans_sample.to_string())

    trans_stats = con.execute(f"""
        SELECT 
            min(trans_start) as min_start, max(trans_start) as max_start,
            min(meter_expire) as min_expire, max(meter_expire) as max_expire,
            min(trans_amt) as min_amt, max(trans_amt) as max_amt,
            avg(trans_amt) as avg_amt, median(trans_amt) as median_amt,
            count(*) filter (uuid is null) as null_uuid,
            count(distinct uuid) as distinct_uuid,
            count(*) filter (pole_id is null) as null_pole,
            count(*) filter (meter_expire <= trans_start) as invalid_duration
        FROM read_csv_auto('{active_trans_file.as_posix()}')
    """).fetchdf()
    print(f"Transaction stats for {active_trans_file.name}:\n", trans_stats.to_string())

    pay_methods = con.execute(f"""
        SELECT pay_method, count(*) as cnt 
        FROM read_csv_auto('{active_trans_file.as_posix()}')
        GROUP BY pay_method
        ORDER BY cnt DESC
    """).fetchdf()
    print("Distinct pay_method values:\n", pay_methods.to_string())

    meter_types = con.execute(f"""
        SELECT meter_type, count(*) as cnt 
        FROM read_csv_auto('{active_trans_file.as_posix()}')
        GROUP BY meter_type
        ORDER BY cnt DESC
    """).fetchdf()
    print("Distinct meter_type values:\n", meter_types.to_string())

    print("\n" + "="*60)
    print("5. JOIN KEY MATCH RATE (pole vs pole_id)")
    print("="*60)
    join_stats = con.execute(f"""
        WITH t_poles AS (
            SELECT DISTINCT pole_id FROM read_csv_auto('{active_trans_file.as_posix()}') WHERE pole_id IS NOT NULL
        ),
        l_poles AS (
            SELECT DISTINCT pole FROM read_csv_auto('{loc_data_file.as_posix()}') WHERE pole IS NOT NULL
        )
        SELECT 
            (SELECT count(*) FROM t_poles) as trans_distinct_poles,
            (SELECT count(*) FROM l_poles) as loc_distinct_poles,
            (SELECT count(*) FROM t_poles JOIN l_poles ON t_poles.pole_id = l_poles.pole) as matched_poles,
            (SELECT count(*) FROM t_poles WHERE pole_id NOT IN (SELECT pole FROM l_poles)) as unmatched_trans_poles,
            (SELECT count(*) FROM l_poles WHERE pole NOT IN (SELECT pole_id FROM t_poles)) as unmatched_loc_poles
    """).fetchdf()
    print("Join statistics:\n", join_stats.to_string())

    return {
        "loc_dict": loc_dict_df,
        "trans_dict": trans_dict_df,
        "loc_schema": loc_schema,
        "trans_schema": trans_schema,
        "coord_stats": coord_stats,
        "config_names": config_names,
        "trans_stats": trans_stats,
        "pay_methods": pay_methods,
        "meter_types": meter_types,
        "join_stats": join_stats
    }

if __name__ == "__main__":
    run_recon()
