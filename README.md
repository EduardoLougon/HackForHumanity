# San Diego Parking Finder — Backend & Data Pipeline

Backend service and data pipeline for San Diego parking meter availability prediction.

### Running M4 chat

Copy `.env.example` to `.env`. Set the Gemini key and model, a server-side key
with Google's Geocoding API enabled, and the exact frontend origin. Point
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
