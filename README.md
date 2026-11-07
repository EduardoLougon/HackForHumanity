# San Diego Parking Finder — Backend & Data Pipeline

Backend service and data pipeline for San Diego parking meter availability prediction.

### Running the M3 parking API

Start the server with `uv run uvicorn parking.api.main:app --port 8000`.
It loads `data/serving/parking.duckdb` at startup. Override that path with
`PARKING_DB_PATH`. Restart after publishing a new snapshot. Check `GET /health`
for `serving_db_loaded` and `GET /v1/meta` for the dataset window and freshness.
Data endpoints return 503 until a snapshot is available.

To publish a snapshot from normalized Parquet exports, run
`uv run python -m parking.transform.build_serving --meters meters.parquet --profiles profiles.parquet --metadata metadata.parquet`.
The inputs use the three serving schemas in section 7 of `backendspec.md`.
The profile export includes the M2 fallback and band for each pole. Weekdays
start at Monday = 0. Buckets start at local midnight and default to 15 minutes.
Set `bucket_minutes` in the single metadata row if the pipeline changes that size.
Use offset-aware timestamps for freshness and build time.

The normalized `enforced_hours_json` is a complete weekly schedule. For example,
`{"0":[[540,1020]]}` means Monday from 09:00 up to 17:00. Split overnight
windows at midnight. Omitted weekdays have no enforcement. Supply SQL NULL
when the schedule is unknown. Without a schedule or a matching profile,
the API reports unknown availability.

`GET /v1/spots?lat=32.7073&lon=-117.1566&duration_minutes=120` returns ranked
estimates. Optional `arrival_time` takes an ISO timestamp with offset.
`GET /v1/meters/{pole_id}` includes the weekly profile for that pole.

### Running M4 chat

Copy `.env.example` to `.env`. Set the Gemini key, model and frontend origin. Named places use the keyless
[Nominatim search API](https://nominatim.org/release-docs/latest/api/Search/) with
[OpenStreetMap contributors](https://www.openstreetmap.org/copyright) data.
Use one chat worker and one replica with the public endpoint. Its [usage policy](https://operations.osmfoundation.org/policies/nominatim/) limits the entire app to one request per second and prohibits autocomplete. Submit only user-requested, non-confidential destinations. The backend caches lookups, identifies the app, and includes attribution in replies. Set `NOMINATIM_SEARCH_URL` to your own search endpoint for multi-worker deployments. Point
`PARKING_API_BASE_URL` at a separate M3 server only when running chat without
a local serving database. With a snapshot loaded, chat uses the same in-process
search as the map.

```sh
uv sync --locked --extra dev
uv run pytest tests
uv run uvicorn parking.api.main:app --port 8000
```

Send `POST /v1/chat` a JSON body such as
`{"messages":[{"role":"user","text":"Parking near Petco Park at 7 tonight for 2 hours"}]}`.
For “near me”, include `user_location` with numeric `lat` and `lon`.
The response contains `reply`, the full ranked `results` for map pins, and
`data_as_of` from M3. Tests use scripted Gemini responses and local fixtures
and run without credentials or a serving database.

## How we used GitHub Copilot

We used GitHub Copilot as a coding assistant while building our parking web app. It helped us draft and refine the code that connects a destination search to the parking map and results list. We tested that flow in the browser by dropping a pin, entering a different destination in chat, and checking that both the pin and the nearby parking options moved to the new location.
