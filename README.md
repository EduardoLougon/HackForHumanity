# San Diego Parking Finder — Backend & Data Pipeline

Backend service and data pipeline for San Diego parking meter availability prediction.

### Running M4 chat

Copy `.env.example` to `.env`. Set the Gemini key, model and frontend origin. Named places use the keyless
[Nominatim search API](https://nominatim.org/release-docs/latest/api/Search/) with
[OpenStreetMap contributors](https://www.openstreetmap.org/copyright) data.
Use one chat worker and one replica with the public endpoint. Its [usage policy](https://operations.osmfoundation.org/policies/nominatim/) limits the entire app to one request per second and prohibits autocomplete. Submit only user-requested, non-confidential destinations. The backend caches lookups, identifies the app, and includes attribution in replies. Set `NOMINATIM_SEARCH_URL` to your own search endpoint for multi-worker deployments. Point
`PARKING_API_BASE_URL` at the M3 service providing `GET /v1/spots` and
`GET /v1/meters/{pole_id}`. Those routes are still under development.

```sh
uv sync --locked --extra dev
uv run pytest tests/test_chat.py
uv run uvicorn parking.api.main:app --port 8001
```

Send `POST /v1/chat` a JSON body such as
`{"messages":[{"role":"user","text":"Parking near Petco Park at 7 tonight for 2 hours"}]}`.
For “near me”, include `user_location` with numeric `lat` and `lon`.
The response contains `reply`, the full ranked `results` for map pins, and
`data_as_of` from M3. Tests use scripted Gemini responses and local fixtures
and run without credentials or a serving database.
