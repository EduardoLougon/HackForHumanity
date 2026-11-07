# San Diego Parking Finder — Backend & Data Pipeline Spec

> **For the agent working on this repo:** read this whole file before writing code. Start with **Milestone 0** (data reconnaissance) and do not build later stages on top of any field, unit, or cadence marked **VERIFY** until you have confirmed it against the real data. When the real data contradicts this spec, update this file and `docs/DATA_NOTES.md`, then continue. Questions marked **ASK HUMAN** need a decision from the project owner before you implement that part.

---

## 1. Product goal

Users open a frontend (map + chat box), say where they are going ("near Petco Park at 7pm for 2 hours", or "near me"), and get a ranked list of nearby metered street parking with an **estimated likelihood** that a space is open when they arrive. A Gemini model handles the natural-language side; this backend owns all data, all math, and all results.

## 2. The most important constraint: what the data can and cannot tell us

The City of San Diego open data portal publishes **meter locations** and **historical meter payment transactions**. Neither dataset is a live occupancy feed, so this system **cannot know which spaces are empty right now**. Reasons the agent must design around:

| Limitation | Consequence |
|---|---|
| Transactions are published in batches with a lag (VERIFIED: Daily cadence, observed lag < 24 hours, max timestamp yesterday) | No real-time sensor signal. We predict from historical patterns. |
| A payment shows someone *paid*, not that they are still parked | Cars leave before expiry; others overstay. |
| Unpaid occupancy is invisible (e.g. disabled-placard holders, who may park at meters without paying in California; non-payers; VERIFIED: Credit Card (58.4%), Cash (23.1%), Remote/Mobile (18.5%), Smart Card (0.01%) are included) | Paid occupancy **underestimates** true occupancy, so raw estimates are optimistic. |
| Multi-space pay stations cover an unknown number of spaces (VERIFIED: No individual space count or stall ID exists in portal data) | Capacity must be estimated for those poles using p95 concurrency heuristic. |
| Outside enforcement hours nobody pays | No data at all for evenings/holidays/free periods. |

**Therefore the product reframes "find empty spots" as "rank nearby meters by historically estimated probability of a free space at your arrival time."** Every API response and every Gemini reply must present results as estimates, never as guarantees. If a future live source appears (sensors, a vendor API), it slots in as an additional signal in Stage 6 without changing the API contract.

## 3. Architecture

```
                ┌────────────────────────── offline / scheduled ──────────────────────────┐
 data.sandiego  │  fetch ──► clean ──► sessionize ──► occupancy grid ──► profiles ──► serving DB │
 .gov CSVs ────►│  (raw)     (parquet)   (parquet)      (parquet)          (parquet)   (duckdb) │
                └────────────────────────────────────────────────────────────────────────┘
                                                                                     │
                ┌────────────────────────── online / request time ───────────────────▼───┐
 Frontend ─────►│ FastAPI                                                                 │
   map  ───────►│  GET  /v1/spots   ── deterministic query (no LLM) ──► results JSON      │
   chat ───────►│  POST /v1/chat    ── Gemini w/ function calling                         │
                │                        ├─ tool: geocode(place)                          │
                │                        ├─ tool: find_parking(lat, lon, time, …) ─► same │
                │                        │                         code path as /v1/spots │
                │                        └─ final text reply + structured results         │
                └────────────────────────────────────────────────────────────────────────┘
```

**Design rule: Gemini never sees the raw datasets and never computes distances, rankings, or probabilities.** LLMs are unreliable at geospatial math and the transaction data is far too large for a prompt. Gemini's only jobs are (a) turning the user's words into structured tool calls and (b) explaining tool results in plain language. The frontend draws map pins from the backend's structured `results`, not from Gemini's text, so a hallucinated coordinate can never reach the map.

## 4. Data sources

Discover download URLs by scraping each dataset page for `.csv` links. **Do not hardcode file URLs**; the transactions dataset is split into per-year files and new files appear over time. Also download each dataset's data dictionary (linked on the page).

### 4.1 Parking meter locations
Page: https://data.sandiego.gov/datasets/parking-meters-locations/
Production spatial snapshot file: `https://seshat.datasd.org/parking_meters_locations/parking_meters_current.csv` (3,619 poles).

Actual columns verified against data dictionary and data:

| Column (verified) | Type | Notes |
|---|---|---|
| `zone`, `area`, `sub-area` | VARCHAR | Geographic grouping. Note hyphen: `sub-area` in CSV header. `sub-area` is the fallback level for sparse poles. |
| `pole` | VARCHAR | Join key to transactions `pole_id`. Alphanumeric string. 3,538 out of 3,619 active meters match 2026 transactions (97.76% match). |
| `configid`, `configname` | BIGINT / VARCHAR | Configuration description. Note: Dataset also explicitly provides structured columns: `time_start`, `time_end`, `time_limit`, `days_in_operation`, `price`, `mobile_pay`, `multi_space`, `restrictions`. |
| `longitude`, `latitude` | DOUBLE | 5 rows out of bounds (near 0 or in test zones/spares), 3,614 valid coordinates in San Diego box (lat 32.5–33.3, lon −117.6 to −116.8). |

### 4.2 Parking meter transactions
Page: https://data.sandiego.gov/datasets/parking-meters-transactions/
Current active file: `https://seshat.datasd.org/parking_meters_transactions/treas_parking_payments_2026_datasd.csv` (751.4 MB, 5,741,874 rows).

Actual columns verified:

| Column (verified) | Type | Notes |
|---|---|---|
| `transaction_id` | BIGINT/VARCHAR | Primary dedupe key (NOT `uuid`). 100% unique in 2026 (5,741,874 distinct values). |
| `meter_type` | VARCHAR | Single-space vs multi-space: values are `"SS"` (82.38%) and `"MS"` (17.62%). |
| `pole_id` | VARCHAR | Join key to locations `pole`. Zero null values. |
| `trans_amt` | DOUBLE | Transaction amount in **DOLLARS** (NOT cents). Range $0.00–$69.35, avg $4.00, median $2.85. |
| `pay_method` | VARCHAR | `Credit Card` (58.43%), `Cash` (23.10%), `Remote` (18.45% - mobile app payments), `Smart Card` (0.01%). Mobile payments ARE included. |
| `date_trans_start`, `date_meter_expire` | TIMESTAMP | Paid interval (named `date_trans_start` and `date_meter_expire`, NOT `trans_start`). Naive local time in `America/Los_Angeles`. 24,742 rows (0.43%) have expire <= start; 847 rows have duration > 12h. |

Milestone 0 findings:
- Stated update cadence: Daily.
- Observed data lag: Max transaction timestamp is `2026-11-06 21:54:00` (< 24 hours lag from 2026-11-07).
- License: City of San Diego Open Data Portal Terms of Use (public access, open for derivative works).

## 5. Tech stack (default — ASK HUMAN if they want something else)

Python 3.11+, DuckDB + Parquet for ETL, FastAPI + Uvicorn for serving, `scikit-learn` BallTree (haversine) or plain NumPy for nearest-neighbor (a few thousand meters fits in memory), the `holidays` package for US/California holidays, `pydantic` for schemas, `pytest` for tests, and Google's official Gen AI Python SDK for Gemini (VERIFY current package name and a current function-calling-capable model in Google's docs; keep the model name in an env var). Everything runs locally with `make build` and `make serve`, and ships as a Dockerfile.

## 6. Repo layout

```
parking-backend/
  pyproject.toml
  Makefile                      # fetch, build, serve, test, backtest
  config.yaml                   # all tunables from section 8
  .env.example                  # GEMINI_API_KEY, GEMINI_MODEL, GEOCODER_*, never commit .env
  Dockerfile
  docs/DATA_NOTES.md            # written in Milestone 0
  src/parking/
    ingest/fetch.py             # scrape dataset pages, download, manifest
    transform/clean.py
    transform/config_parser.py
    transform/sessionize.py
    transform/occupancy.py
    transform/profiles.py
    transform/build_serving.py  # writes data/serving/parking.duckdb
    serve/store.py              # loads serving DB + spatial index at startup
    serve/availability.py       # the single find_parking implementation
    api/main.py
    api/routes_spots.py
    api/routes_chat.py
    llm/gemini_client.py
    llm/tools.py                # function declarations + dispatch
    llm/prompts.py
    geo/geocode.py
  scripts/build_all.py
  reports/                      # data-quality + backtest reports
  tests/
  data/{raw,clean,serving}/     # gitignored
```

## 7. Pipeline stages

### Stage 1 — Ingest (`fetch.py`)
Scrape both dataset pages for CSV and dictionary links; download to `data/raw/<dataset>/<filename>`. Skip unchanged files using `ETag`/`Last-Modified` or a SHA-256 comparison. Write `data/raw/manifest.json` with url, filename, downloaded_at, sha256, bytes, and row count. Only the trailing profile window (section 8) is needed for the product; downloading older years is optional and configurable.

### Stage 2 — Clean (`clean.py`) → `data/clean/meters.parquet`, `data/clean/transactions.parquet`
For transactions: parse timestamps to timezone-aware `America/Los_Angeles`; dedupe on `uuid`; drop rows with null `pole_id`, `meter_expire <= trans_start`, or duration above `max_session_hours`; move rows whose `pole_id` is not in the meters file to a quarantine table rather than silently dropping them. For meters: validate coordinates, normalize `meter_type` to `single_space | multi_space | unknown`, and parse `rate_per_hour`, `max_minutes`, and enforced hours from config text where possible (null when unparseable — never guess). Every run writes `reports/dq_<date>.md` with counts for each rule, quarantine size, and coverage by area.

### Stage 3 — Sessionize (`sessionize.py`) → `data/clean/sessions.parquet`
For **single-space** poles, merge transactions on the same pole whose intervals overlap or are separated by ≤ `merge_gap_minutes` into one session (these are extensions/top-ups, not new cars). For **multi-space** poles, transactions are kept as separate concurrent sessions because they may be different spaces; if Milestone 0 finds a space identifier, merge per space instead. Output columns: `pole_id, session_start, session_end, n_transactions`.

### Stage 4 — Occupancy grid (`occupancy.py`) → `data/clean/occupancy.parquet`
Split each day into `bucket_minutes` buckets (default 15). For every (pole, date, bucket) **inside enforcement hours**, compute occupied fraction:

- single-space: `min(1, Σ overlap_seconds / bucket_seconds)`
- multi-space: `Σ overlap_seconds / bucket_seconds / capacity_est`, capped at 1, where `capacity_est` is the `capacity_percentile` (default p95) of concurrent sessions for that pole over the window. ASK HUMAN whether a real space-count source exists.

**Critical pitfall:** the grid must include buckets with **zero** sessions. Averaging only over buckets that had payments inflates occupancy badly. Build the full (pole × enforced date × enforced bucket) grid, left-join the session overlaps, and fill missing with 0.

Enforcement hours come from parsed config when available. Fallback: treat (sub_area, day_of_week, bucket) as enforced if transactions start there on at least `enforced_min_date_share` of that weekday's dates in the window.

DuckDB sketch for the overlap step (adapt to verified schema):

```sql
WITH exploded AS (
  SELECT pole_id, session_start, session_end,
         unnest(generate_series(
           time_bucket(INTERVAL 15 MINUTE, session_start),
           session_end - INTERVAL 1 MICROSECOND,
           INTERVAL 15 MINUTE)) AS bucket_start
  FROM sessions
)
SELECT pole_id, bucket_start,
       SUM(date_diff('second',
             GREATEST(session_start, bucket_start),
             LEAST(session_end, bucket_start + INTERVAL 15 MINUTE))) / 900.0 AS occupied_fraction
FROM exploded
GROUP BY ALL;
```

### Stage 5 — Profiles (`profiles.py`) → `data/clean/profiles.parquet`
Aggregate the grid to (pole_id, day_of_week, bucket_of_day) using an exponentially recency-weighted mean (half-life `recency_half_life_weeks`) over the trailing `profile_window_weeks`, excluding US/California holidays. Store `mean_occupancy`, `std_occupancy`, and `n_dates` (distinct dates observed). Build the same profile at `sub_area` level as a fallback. Then:

- `p_available = clamp(1 − mean_occupancy − unpaid_occupancy_uplift, 0, 1)`; the uplift defaults to 0 and is a documented knob for correcting the optimistic bias if ground truth ever appears.
- Use the pole profile if `n_dates ≥ min_dates_pole`, otherwise the sub_area profile (`basis = "sub_area"`).
- `confidence`: `high` = pole basis with `n_dates ≥ 12`; `medium` = pole basis below that, or sub_area basis with `n_dates ≥ 12`; `low` otherwise.
- **Out-of-service detection:** a pole with zero transactions in the last `inactive_days` while its sub_area had normal activity is flagged `possibly_out_of_service` (a broken meter looks "always empty" and must not be recommended).

### Stage 6 — Serving DB (`build_serving.py`) → `data/serving/parking.duckdb`
Tables: `meters(pole_id, lat, lon, zone, area, sub_area, meter_type, rate_per_hour, max_minutes, enforced_hours_json, status)`, `availability_profile(pole_id, dow, bucket, p_available, band, confidence, basis, n_dates)`, and `metadata(data_as_of, built_at, profile_window_start, profile_window_end, pipeline_version)`, where `data_as_of` is the max `trans_start` seen. Write to a temp file and atomically rename so the running API never reads a half-built DB.

## 8. Config defaults (`config.yaml`)

```yaml
timezone: America/Los_Angeles
bucket_minutes: 15
max_session_hours: 12
merge_gap_minutes: 2
capacity_percentile: 95
enforced_min_date_share: 0.5
profile_window_weeks: 26
recency_half_life_weeks: 8
min_dates_pole: 6
inactive_days: 14
unpaid_occupancy_uplift: 0.0
bands: { likely: 0.6, possible: 0.3 }   # p >= 0.6 likely, >= 0.3 possible, else unlikely
search:
  default_radius_m: 400
  max_radius_m: 2000
  default_max_results: 10
  distance_decay_m: 250                  # score = p_available * exp(-distance / decay)
  walk_speed_m_per_min: 80
```

## 9. Availability query (`availability.py`)

One function, used by both `/v1/spots` and the Gemini `find_parking` tool:

`find_parking(lat, lon, arrival_time=None, duration_minutes=None, radius_m=400, max_results=10)`

1. Validate: coordinates inside the San Diego bounding box, radius clamped to `[50, max_radius_m]`, `arrival_time` defaults to now in local time.
2. Candidates: meters within `radius_m` via the in-memory haversine index, excluding `possibly_out_of_service`.
3. If `duration_minutes` exceeds a meter's known `max_minutes`, exclude it; if `max_minutes` is unknown, keep it and add a note.
4. Map arrival to (dow, bucket). If the bucket is outside enforcement hours (or a holiday), return the meter with `status: "unmetered_now"` and `p_available: null` — parking is likely free then but we have no occupancy data.
5. Score `p_available * exp(-distance_m / distance_decay_m)`, sort descending, return top `max_results`. Straight-line `walk_minutes = distance_m / walk_speed_m_per_min` (label it as approximate).

Response shape (example values are illustrative only):

```json
{
  "query": { "lat": 32.7073, "lon": -117.1566, "arrival_time": "2026-11-07T19:00:00-08:00",
             "duration_minutes": 120, "radius_m": 400 },
  "data_as_of": "2026-11-01T23:59:00-07:00",
  "results": [
    {
      "pole_id": "EXAMPLE-123",
      "lat": 32.7081, "lon": -117.1559,
      "distance_m": 110, "walk_minutes": 1.4,
      "status": "metered",
      "availability": { "p_available": 0.64, "band": "likely", "confidence": "high", "basis": "pole" },
      "meter_type": "single_space",
      "rate_per_hour": null, "max_minutes": 120,
      "area": "EXAMPLE", "sub_area": "EXAMPLE"
    }
  ],
  "notes": ["Estimates come from historical meter payments, not live sensors."]
}
```

## 10. HTTP API (`api/`)

| Endpoint | Purpose |
|---|---|
| `GET /health` | Liveness + whether the serving DB loaded. |
| `GET /v1/meta` | `data_as_of`, `built_at`, window, meter count. Frontend shows data freshness. |
| `GET /v1/spots?lat&lon&arrival_time&duration_minutes&radius_m&limit` | Deterministic search for the map; no LLM involved. |
| `GET /v1/meters/{pole_id}` | Detail view, including the pole's weekly profile for a small chart. |
| `POST /v1/chat` | Body: `{ messages: [{role, text}], user_location?: {lat, lon} }`. Returns `{ reply, results, data_as_of }`. |

The backend is stateless; the frontend sends the recent message history (cap at the last ~10 turns). Enable CORS for the frontend origin only, and rate-limit `/v1/chat` per client because each call costs Gemini tokens.

## 11. Gemini integration (`llm/`)

The `GEMINI_API_KEY` lives only on the server. The frontend never calls Gemini directly.

**Request loop:** build the system prompt (below) with the current local time and user location → send history + tool declarations → while Gemini returns function calls (max 4 rounds): validate args, execute, send back a **compact** result (top results, trimmed fields) → return Gemini's final text as `reply` and the full structured output of the last `find_parking` call as `results`. If Gemini mentions a `pole_id` not present in tool output, log it and drop that sentence or regenerate.

**Tool declarations** (JSON-schema style; adapt to the SDK's format):

```json
[
  {
    "name": "geocode",
    "description": "Convert a place name or address in San Diego into coordinates.",
    "parameters": { "type": "object",
      "properties": { "query": { "type": "string" } }, "required": ["query"] }
  },
  {
    "name": "find_parking",
    "description": "Find metered street parking near a point in San Diego, ranked by estimated likelihood of an open space at arrival and walking distance. Estimates come from historical payment data, not live sensors.",
    "parameters": { "type": "object",
      "properties": {
        "lat": { "type": "number" },
        "lon": { "type": "number" },
        "arrival_time": { "type": "string", "description": "ISO 8601 with offset in America/Los_Angeles. Omit for now." },
        "duration_minutes": { "type": "integer" },
        "radius_m": { "type": "integer", "description": "Default 400, max 2000." },
        "max_results": { "type": "integer" }
      },
      "required": ["lat", "lon"] }
  },
  {
    "name": "get_meter_details",
    "description": "Get rate, time limit, enforced hours, and weekly availability pattern for one meter.",
    "parameters": { "type": "object",
      "properties": { "pole_id": { "type": "string" } }, "required": ["pole_id"] }
  }
]
```

**Geocoder:** ASK HUMAN which provider (e.g. Google Geocoding API, Mapbox, or Nominatim, whose public instance has strict usage limits). Bias or restrict results to San Diego, and cache results.

**System prompt draft (`prompts.py`):**

```
You are the assistant inside a San Diego street-parking finder.
Current local time: {now_local} (America/Los_Angeles). User location: {user_location_or_unknown}.

- Answer parking questions only by calling tools. Never invent meters, addresses, prices, times, or probabilities.
- If the user names a place, call geocode, then find_parking. For "near me", use the user location; if it is unknown, ask where they are headed.
- Convert relative times ("tonight", "Saturday at 7") into an ISO arrival_time before calling find_parking.
- Describe availability with the returned band and confidence ("likely open", "possible", "unlikely"). These are estimates from historical payment patterns, not live data; say so once per conversation.
- If a result has status unmetered_now, explain that meters are not enforced then, so parking is likely free, but there is no data on how full the street is.
- Keep replies short: the top 3 options with distance and walk time. The app shows the full list on the map.
- Coverage is City of San Diego metered street parking only.
```

## 12. Refresh & operations

A scheduled job (cron or GitHub Actions) runs `fetch → clean → sessionize → occupancy → profiles → build_serving`, at a cadence matching the portal's real update frequency found in Milestone 0 (no point running daily if files change monthly). The API reloads the serving DB when the file changes. Alert (log at minimum) if `data_as_of` is older than twice the expected cadence.

## 13. Testing & validation

Unit tests cover: the config parser against real `config_name` samples; sessionization fixtures (extension, overlapping top-up, gap above threshold, multi-space concurrency); bucket math (a session 10:05–10:35 yields 0.667, 1.0, 0.333 for the 10:00, 10:15, 10:30 buckets); zero-filled grid (a pole with one session in a week must not show 100% occupancy); DST transition days; radius search and bounding-box validation; tool argument validation and clamping.

**Backtest (`make backtest`):** build profiles from the window excluding the most recent 4 weeks, predict paid occupancy for those 4 weeks, and report MAE and a calibration table by band in `reports/backtest_<date>.md`. The model must beat a naive baseline (each pole's overall mean occupancy, ignoring day and time). Note in the report that this validates against *paid* occupancy only.

## 14. Milestones

**M0 — Data reconnaissance (no app code).**
- [x] Download the locations file and the most recent transactions file plus dictionaries.
- [x] Write `docs/DATA_NOTES.md`: actual columns and types, `trans_amt` units, timestamp timezone, `meter_type` and `pay_method` values, `config_name` patterns, any space IDs or capacities, join-key match rate between `pole` and `pole_id`, row counts per year, stated update cadence vs observed lag, license.
- [x] Update every VERIFY item in this file. **Stop and summarize findings for the human before M1.**

**M1 — Ingest + clean.** [x] `make fetch && make build` produces clean parquet files and a DQ report; join match rate and quarantine counts are reported.

**M2 — Sessions, occupancy, profiles, backtest.** [x] All unit tests pass; backtest beats the naive baseline; out-of-service poles are flagged.

**M3 — Serving + `/v1/spots`.** [x] p95 latency under 200 ms locally (~29 ms achieved); response matches section 9.

**M4 — `/v1/chat` with Gemini.** [x] Scripted conversations ("near Petco Park tonight for 2 hours", "near me" with and without location, a place outside San Diego, a time outside enforcement hours) produce tool calls with correct arguments and replies that cite only tool output.

**M5 — Ops.** [x] Dockerfile, render.yaml, scheduled refresh with atomic DB swap, freshness alert, `README.md` with setup steps.

## 15. Open questions (ASK HUMAN)

- [x] Backend language/stack OK as Python + FastAPI + DuckDB? -> Yes, confirmed.
- [ ] Geocoding provider and budget? -> Defaulting to Nominatim (OpenStreetMap) with strict User-Agent and caching; Google Geocoding available if API key is provided.
- [x] Deployment target (Cloud Run, Fly.io, a VM, etc.)? -> Render (configured via Dockerfile / render.yaml / web service).
- [ ] Show users a percentage, or only bands (likely/possible/unlikely)? Bands are recommended given the paid-only bias.
- [x] Any source for space counts at multi-space pay stations, or for garages/lots, to add later? -> None in portal data; proceeding with p95 concurrent sessions capacity estimation.

## 16. Limitations the frontend must surface

Results are historical estimates, not live availability; paid data misses placard and non-paying parkers, so estimates lean optimistic; meters map to poles, not individual painted spaces; unmetered streets, garages, and private lots are not covered; and data freshness is shown from `/v1/meta`.