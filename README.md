# ParkingPilot SD

ParkingPilot SD helps you decide which San Diego street to try first when you need to park. Tell it where you're headed, when you'll arrive, and how long you plan to stay. The map shows nearby meters so you can weigh the chance of finding a space against the walk to your destination.

## Try a parking search

Imagine you're meeting a friend for lunch in Little Italy. Type **“Find parking near Piazza Basilone on Tuesday at 1 pm for two hours”** in the chat, or drop a pin on the map. The parking list shows the estimated walk and, where available, the meter rate and time limit. If the closest meter looks busy, you can check whether walking another block gives you better odds. Change your destination in chat and the pin and list move together.

The colors on the map and in the list use the same scale.

| Pin | What it tells you |
| --- | --- |
| Green | A space is estimated to be **likely** at your arrival time (60% or higher). |
| Amber-brown | A space is **possible** (30% to under 60%). |
| Red | A space is **unlikely** (under 30%). |

Blue marks reported off-hours. Gray means there isn't enough availability information. You can also explore mapped lots and garages. The availability estimates apply to street meters.

To try the map and chat on your laptop, follow the [web app setup instructions](https://github.com/LiamAvigani/App1#run-locally). This repository contains the service that supplies the parking results.

## Where the estimates come from

The City of San Diego publishes parking meter locations and payment records. We use those records to estimate how busy each meter usually is on a given day of the week and time of day, giving more weight to recent weeks. For a Tuesday lunch search, what happened around lunchtime on previous Tuesdays is more useful than a citywide average.

A payment records how much time someone bought. A driver might leave early, and some parked cars never appear in the payment records. That's why even a green pin is an estimate. Check the posted signs when you arrive.

## How we used GitHub Copilot

We used GitHub Copilot to help build the web app, including the code that connects a destination search to the map and parking list. We tested that interaction by dropping a pin, entering a different destination in chat, and checking that both the pin and the nearby parking options moved to the new location.

## For developers

The backend runs on Python 3.11+ and FastAPI. The map and Gemini chat assistant use the same parking search. The [backend specification](backendspec.md) describes how we turn payment records into estimates and rank nearby meters.

<details>
<summary>Run the backend locally</summary>

Install Python 3.11+ and [uv](https://docs.astral.sh/uv/), then run these commands.

```bash
git clone https://github.com/EduardoLougon/HackForHumanity.git
cd HackForHumanity
uv sync --extra dev
cp .env.example .env
uv run uvicorn src.parking.api.main:app --port 8000 --reload
```

For chat, set `GEMINI_API_KEY` in `.env` before starting the server. Open the [interactive API docs](http://localhost:8000/docs) to try a request.

</details>

<details>
<summary>API, tests, and deployment</summary>

| Endpoint | Purpose |
| --- | --- |
| `GET /v1/spots` | Find nearby meters for a destination, arrival time, and length of stay. |
| `GET /v1/meters/{pole_id}` | See a meter's details and weekly availability pattern. |
| `POST /v1/chat` | Ask a parking question and receive a reply and map results. |
| `GET /v1/meta` | Check how recent the data is. |
| `GET /health` | Check whether the service is ready. |

```bash
uv run pytest tests/ -v                   # Run the tests
uv run python scripts/benchmark_spots.py  # Measure search latency
uv run python scripts/backtest.py         # Evaluate estimates on held-out weeks
uv run python scripts/build_all.py        # Refresh the parking dataset
```

To deploy on Render, create a Blueprint from this repository and set `GEMINI_API_KEY`. The [Render configuration](render.yaml) defines the Docker service and its health check.

</details>
