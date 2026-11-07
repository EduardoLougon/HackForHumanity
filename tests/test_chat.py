"""Scripted conversation and tool calling tests for /v1/chat endpoint."""

import pytest
from fastapi.testclient import TestClient

from src.parking.api.main import app
from src.parking.geo.geocode import geocode_place
from src.parking.llm.tools import tool_find_parking, tool_geocode, tool_get_meter_details
from src.parking.serve.store import store


@pytest.fixture(scope="module", autouse=True)
def client():
    store.load()
    with TestClient(app) as c:
        yield c


def test_tool_geocode_san_diego():
    res = tool_geocode("Petco Park")
    assert "lat" in res
    assert "lon" in res
    assert 32.5 <= res["lat"] <= 33.3
    assert -117.6 <= res["lon"] <= -116.8


def test_tool_geocode_outside_san_diego():
    res = tool_geocode("Eiffel Tower Paris France")
    assert "error" in res


def test_tool_find_parking_execution():
    res = tool_find_parking(lat=32.7073, lon=-117.1566, duration_minutes=120)
    assert res["status"] == "success"
    assert len(res["top_spots"]) > 0
    first = res["top_spots"][0]
    assert "pole_id" in first
    assert "band" in first
    assert "distance_m" in first


def test_tool_get_meter_details():
    pole_id = list(store.meters_by_id.keys())[0]
    res = tool_get_meter_details(pole_id)
    assert res["pole_id"] == pole_id
    assert "meter_type" in res


def test_chat_endpoint_petco_park(client):
    req_body = {
        "messages": [
            {"role": "user", "text": "Find parking near Petco Park tonight for 2 hours"}
        ]
    }
    r = client.post("/v1/chat", json=req_body)
    assert r.status_code == 200
    data = r.json()
    assert "reply" in data
    assert "results" in data
    assert len(data["results"]) > 0
    assert "historical" in data["reply"].lower() or "sensor" in data["reply"].lower()


def test_chat_endpoint_near_me_with_location(client):
    req_body = {
        "messages": [
            {"role": "user", "text": "Where can I park near me?"}
        ],
        "user_location": {"lat": 32.7157, "lon": -117.1611}  # Downtown SD
    }
    r = client.post("/v1/chat", json=req_body)
    assert r.status_code == 200
    data = r.json()
    assert len(data["results"]) > 0


def test_chat_rate_limiting(client):
    req_body = {"messages": [{"role": "user", "text": "parking"}]}
    # Make requests up to rate limit
    for _ in range(35):
        r = client.post("/v1/chat", json=req_body)
    assert r.status_code == 429

