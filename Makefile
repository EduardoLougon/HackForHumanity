.PHONY: install fetch clean sessionize occupancy profiles build serve test backtest benchmark

install:
	uv sync --extra dev

fetch:
	uv run python -m src.parking.ingest.fetch

clean-data:
	uv run python -m src.parking.transform.clean

sessionize:
	uv run python -m src.parking.transform.sessionize

occupancy:
	uv run python -m src.parking.transform.occupancy

profiles:
	uv run python -m src.parking.transform.profiles

build:
	uv run python scripts/build_all.py

serve:
	uv run uvicorn src.parking.api.main:app --host 0.0.0.0 --port 8000 --reload

test:
	uv run pytest tests/ -v

backtest:
	uv run python scripts/backtest.py

benchmark:
	uv run python scripts/benchmark_spots.py

