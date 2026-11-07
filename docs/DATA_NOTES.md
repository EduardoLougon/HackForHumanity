# San Diego Parking Data Reconnaissance Notes (Milestone 0)

*Generated on November 7, 2026 following empirical inspection of portal datasets.*

---

## 1. Datasets Inspected & Download URLs

- **Meter Locations**:
  - Portal Page: `https://data.sandiego.gov/datasets/parking-meters-locations/`
  - Active Snapshot File: `https://seshat.datasd.org/parking_meters_locations/parking_meters_current.csv` (771 KB)
  - Data Dictionary: `https://seshat.datasd.org/parking_meters_locations/meters_locations_dictionary_datasd.csv`
  - *Note on Historical Locations*: `treas_parking_meters_loc_datasd.csv` (3.6 MB) contains historical configuration date intervals (`start_date`, `end_date`), but **lacks latitude and longitude**. The production spatial snapshot is `parking_meters_current.csv`.

- **Meter Transactions**:
  - Portal Page: `https://data.sandiego.gov/datasets/parking-meters-transactions/`
  - Active File (2026): `https://seshat.datasd.org/parking_meters_transactions/treas_parking_payments_2026_datasd.csv` (751.4 MB, 5,741,874 rows)
  - Data Dictionary: `https://seshat.datasd.org/parking_meters_transactions/meters_transactions_dictionary_datasd.csv`
  - Prior Year Files Available: 2021 (~710 MB), 2022 (~774 MB), 2023 (~766 MB), 2024 (~765 MB), 2025 (~778 MB).

- **Stated Cadence vs. Observed Lag**:
  - Stated Update Frequency on Portal: **Daily**
  - Observed Max Transaction Timestamp: `2026-11-06 21:54:00` (lag is **< 24 hours** from current date `2026-11-07`).
  - Locations File Last-Modified: `2026-11-07 01:00:23 GMT` (updated daily).

- **Portal License**:
  - City of San Diego Open Data Portal Terms of Use (`/help/guides/terms/`). Unrestricted public download and derivative works; no warranties.

---

## 2. Locations Schema & Verification (`parking_meters_current.csv`)

| Column Name | Actual Type | Notes & Observed Values |
|---|---|---|
| `zone` | `VARCHAR` | Downtown (2,118), Uptown (956), Mid-City (344), City (71), Balboa Park (61), Pacific Beach (29), San Ysidro (25), Test Zone (14), Spares (1). |
| `area` | `VARCHAR` | Neighborhood designation (e.g. `Core - Columbia`, `Gaslamp`, `Hillcrest`, `East Village`). |
| `sub-area` | `VARCHAR` | **Note hyphen**: `sub-area` in CSV header. Represents block and street (e.g. `1300 FIRST AVE`). |
| `pole` | `VARCHAR` | Primary key joining to transactions `pole_id`. Format: alphanumeric with hyphens (e.g. `1-1315`, `5-800W`, `BD-9`). 3,619 distinct poles. |
| `latitude` | `DOUBLE` | Latitude in WGS84. Valid range 32.5 to 33.3. 5 rows are out of bounds (near 0 or in test zones). |
| `longitude` | `DOUBLE` | Longitude in WGS84. Valid range -117.6 to -116.8. |
| `configid` | `BIGINT` | Integer configuration ID. |
| `configname` | `VARCHAR` | Human-readable configuration description (e.g. `Downtown MK5 - 2 Hour Max $2.50 HR, 8am-8pm, Mon-Sat, Prepay, NFC`). |
| `time_start` | `VARCHAR` | Structured opening time: `8am` (2,706), `10am` (863), `8:30am` (13), `9am` (7), `NULL` (30). |
| `time_end` | `VARCHAR` | Structured closing time: `8pm` (2,821), `10pm` (601), `6pm` (113), `4pm` (53), `NULL` (30). |
| `time_limit` | `VARCHAR` | Structured maximum stay: `2 hour` (2,670), `4 hour` (503), `9 hour` (217), `1 hour` (152), `10 hour` (1), `30 min` (1), `NULL` (75). |
| `days_in_operation`| `VARCHAR` | Days active: `Mon-Sat` (3,337), `Mon-Sun` (171), `Mon-Fri` (81), `NULL` (30). |
| `price` | `VARCHAR` | Parking rate: `$2.50 HR` (3,527), `$2.50 HR; $10.00 DAY` (26), `$10.00 4HR; $16 DAY` (16), `$10.00 DAY` (15), `NULL` (31). |
| `mobile_pay` | `VARCHAR` | `Y` (1,100 poles), `NULL` (2,519 poles). |
| `multi_space` | `VARCHAR` | `'Y'` for Multi Space (142 poles, 3.9%), `NULL` for Single Space (3,477 poles, 96.1%). |
| `restrictions` | `VARCHAR` | Additional parking restriction flag (`Y` or `NULL`). |

---

## 3. Transactions Schema & Verification (`treas_parking_payments_2026_datasd.csv`)

| Column Name | Actual Type | Notes & Observed Values |
|---|---|---|
| `transaction_id` | `BIGINT` / `VARCHAR` | **Deduplication key** (NOT `uuid`). All 5,741,874 rows are non-null and 100% unique in 2026. |
| `pole_id` | `VARCHAR` | Join key to `pole` in meters. Zero null values in 2026. |
| `meter_type` | `VARCHAR` | Values are `"SS"` (Single Space: 4,729,892 rows, 82.38%) and `"MS"` (Multi Space: 1,011,982 rows, 17.62%). |
| `date_trans_start` | `TIMESTAMP` | Timestamp when payment began (NOT `trans_start`). Naive local time `America/Los_Angeles`. Min: `2026-01-01 00:06:49`, Max: `2026-11-06 21:54:00`. |
| `date_meter_expire` | `TIMESTAMP` | Timestamp when payment expires (NOT `meter_expire`). |
| `trans_amt` | `DOUBLE` | Transaction amount in **DOLLARS** (NOT cents). Range: $0.00 to $69.35. Average: $4.00, Median: $2.85. |
| `pay_method` | `VARCHAR` | Payment channel: `Credit Card` (58.43%), `Cash` (23.10%), `Remote` (18.45% - mobile app payments), `Smart Card` (0.01%). Mobile payments are included! |
| `zone` | `VARCHAR` | Geographic district (redundantly included in transactions). |
| `area` | `VARCHAR` | Geographic area (redundantly included in transactions). |
| `sub_area` | `VARCHAR` | Block and street (redundantly included in transactions). |

---

## 4. Join Key Analysis (`pole` vs `pole_id`)

- Total distinct active poles in `parking_meters_current.csv`: **3,619**
- Total distinct poles in 2026 transactions: **4,269**
- Exact matched poles: **3,538**
  - **97.76%** of all current meters have recorded payments in 2026.
  - The 81 current meters without 2026 payments are primarily spares, new installations, or out-of-service poles.
- Transactions with poles not in current snapshot:
  - 731 `pole_id`s in transactions do not appear in `parking_meters_current.csv`.
  - These represent decommissioned or re-striped meters from earlier in 2026.
  - Per spec Stage 2, these rows will be quarantined into `data/clean/quarantine_transactions.parquet` rather than silently dropped.

---

## 5. Multi-Space Capacity & Space Identifiers

- **Empirical check for space numbers**: Neither the locations file nor the transactions file contains a stall/space ID (e.g. space 1, space 2). A transaction on a multi-space pole only identifies the pole (e.g. `5-800W`).
- **Conclusion**: We cannot sessionize multi-space transactions by individual parking stall. As prescribed in Stage 4 of `backendspec.md`, multi-space poles must estimate capacity using the 95th-percentile concurrent overlapping sessions heuristic over the profile window.

---

## 6. Data Cleaning Rules & Thresholds Derived from Reconnaissance

1. **Coordinate Filtering**: Drop/flag 5 rows in locations where `latitude < 32.5` or `latitude > 33.3` or `longitude < -117.6` or `longitude > -116.8`, or where `zone = 'Spares'`.
2. **Transaction Validity**:
   - Drop rows where `date_meter_expire <= date_trans_start` (24,742 rows in 2026, ~0.43%).
   - Drop rows where duration exceeds 12 hours (847 rows in 2026, ~0.01%).
   - Deduplicate on `transaction_id`.
3. **Column Renaming / Aliasing**:
   - Meters: Alias `sub-area` to `sub_area`, `configid` to `config_id`, `configname` to `config_name`.
   - Transactions: Map `date_trans_start` -> `trans_start`, `date_meter_expire` -> `meter_expire`, `transaction_id` -> `uuid`. Map `meter_type`: `'SS'` -> `'single_space'`, `'MS'` -> `'multi_space'`.

