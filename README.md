# San Diego Parking Finder — Backend & Data Pipeline

Deterministic parking spot ranking and conversational AI assistant for City of San Diego metered street parking, predicting arrival availability from historical meter payment patterns.

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

---

## 1. Features

- **Empirical Probability Ranking**: Discretizes historical transactions into 15-minute buckets with an exponential recency-weighted mean (8-week half-life) over trailing 26 weeks.
- **Sub-30ms Query Latency**: Scikit-Learn `BallTree` (Haversine metric) spatial index coupled with indexed DuckDB lookups delivers **~29 ms p95 response time** locally.
- **Arrival Time Flexibility**: Search for "now" or arbitrary future times (e.g. "tomorrow at 7 PM", "Saturday afternoon") mapped to the empirical Day-of-Week profile with California holiday awareness.
- **Unmetered & Off-Hours Support**: Outside enforcement hours, returns `status: "unmetered_now"`, `p_available: null` and notes explaining parking is free/unenforced.
- **Duration Policy**: If requested duration exceeds meter time limits, the spot is preserved with `limit_exceeded: true` and an informational note rather than discarded.
- **Gemini Chat Assistant (`POST /v1/chat`)**: Multi-turn tool-calling loop using Google GenAI SDK. Gemini converts user intent into structured tool calls (`geocode`, `find_parking`) while map pins are drawn strictly from backend math.
- **Ready for Render Deployment**: Includes multi-stage `Dockerfile` and `render.yaml`.

---

## 2. API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Liveness & readiness probe (`status`, `meter_count`, DB connection). |
| `GET` | `/v1/meta` | Data freshness (`data_as_of`), bounding box, active meter count. |
| `GET` | `/v1/spots?lat&lon&arrival_time&duration_minutes&radius_m&limit` | Deterministic spatial search ranking nearby parking spots. |
| `GET` | `/v1/meters/{pole_id}` | Detailed meter profile and weekly hourly availability patterns. |
| `POST` | `/v1/chat` | Conversational assistant powered by Google Gemini and tool calling. |

### Sample Response (`GET /v1/spots`)

```json
{
  "query": {
    "lat": 32.7073,
    "lon": -117.1566,
    "arrival_time": "2026-11-09T14:00:00-08:00",
    "duration_minutes": 60,
    "radius_m": 400,
    "max_results": 10
  },
  "data_as_of": "2026-11-06T21:54:00",
  "results": [
    {
      "pole_id": "7-724",
      "lat": 32.7081,
      "lon": -117.1559,
      "distance_m": 110.2,
      "walk_minutes": 1.4,
      "status": "metered",
      "availability": {
        "p_available": 0.64,
        "band": "likely",
        "confidence": "high",
        "basis": "pole"
      },
      "meter_type": "single_space",
      "rate_per_hour": 2.50,
      "max_minutes": 120,
      "limit_exceeded": false,
      "area": "East Village",
      "sub_area": "700 SEVENTH AVE"
    }
  ],
  "notes": [
    "Estimates come from historical meter payments, not live sensors.",
    "Walking times are straight-line approximations."
  ]
}
```

---

## 3. Quickstart & Local Setup

### Prerequisites
- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (recommended) or standard `pip`

### Installation
```bash
# Clone the repository
git clone https://github.com/HackForHumanity.git
cd HackForHumanity

# Install dependencies and dev tools
uv sync --extra dev

# Copy environment template and add your GEMINI_API_KEY
cp .env.example .env
```

### Running the API
```bash
# Start the FastAPI server
uv run uvicorn src.parking.api.main:app --host 0.0.0.0 --port 8000 --reload
```
Interactive API documentation will be accessible at [http://localhost:8000/docs](http://localhost:8000/docs).

---

## 4. Pipeline Execution & Testing

```bash
# Run complete test suite (27 unit & integration tests)
uv run pytest tests/ -v

# Run latency benchmark (confirms p95 < 200 ms)
uv run python scripts/benchmark_spots.py

# Run backtest against 4-week holdout
uv run python scripts/backtest.py

# Full end-to-end refresh pipeline
uv run python scripts/build_all.py
```

---

## 5. Deployment to Render

The repository is configured for one-click or Git-connected deployment on **Render**:

1. Push this repository to GitHub.
2. In the [Render Dashboard](https://dashboard.render.com), click **New** > **Blueprint**.
3. Connect your GitHub repository. Render will automatically detect `render.yaml`.
4. In the Environment Variables settings on Render, set:
   - `GEMINI_API_KEY`: Your Google Gemini API key.
5. Click **Apply**. Render will build the Docker container and deploy the web service.

Alternatively, create a **Web Service** on Render selecting **Docker** runtime, setting the health check path to `/health`.
