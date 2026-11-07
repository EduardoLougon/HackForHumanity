"""Unit tests for availability engine find_parking."""

import pytest
from src.parking.serve.availability import find_parking, is_enforced, parse_arrival_datetime
from src.parking.serve.store import store


@pytest.fixture(scope="module", autouse=True)
def init_store():
    store.load()


def test_find_parking_petco_park():
    # Petco Park coordinates: 32.7073, -117.1566
    res = find_parking(
        lat=32.7073,
        lon=-117.1566,
        arrival_time="2026-11-09T14:00:00-08:00",  # Monday 2 PM
        duration_minutes=60,
        radius_m=400,
        max_results=5,
    )
    assert res is not None
    assert "query" in res
    assert "results" in res
    assert len(res["results"]) > 0
    top = res["results"][0]
    assert "pole_id" in top
    assert top["status"] == "metered"
    assert top["availability"]["p_available"] is not None
    assert top["distance_m"] <= 400
    assert top["walk_minutes"] > 0


def test_find_parking_out_of_bounds():
    with pytest.raises(ValueError, match="outside San Diego"):
        find_parking(lat=37.7749, lon=-122.4194)  # San Francisco


def test_find_parking_unmetered_off_hours():
    # Sunday evening at 10 PM
    res = find_parking(
        lat=32.7073,
        lon=-117.1566,
        arrival_time="2026-11-08T22:00:00-08:00",
        duration_minutes=60,
        radius_m=400,
        max_results=5,
    )
    assert len(res["results"]) > 0
    top = res["results"][0]
    assert top["status"] == "unmetered_now"
    assert top["availability"]["p_available"] is None


def test_find_parking_duration_limit_exceeded():
    # Request 300 minutes (5 hours) in downtown where meters are typically 2 hours
    res = find_parking(
        lat=32.7073,
        lon=-117.1566,
        arrival_time="2026-11-09T12:00:00-08:00",
        duration_minutes=300,
        radius_m=400,
        max_results=5,
    )
    assert len(res["results"]) > 0
    # Spot should be returned with limit_exceeded=True and note attached
    exceeded_spots = [r for r in res["results"] if r.get("limit_exceeded")]
    assert len(exceeded_spots) > 0
    assert "note" in exceeded_spots[0]

