"""Integration tests for FastAPI endpoints."""

import pytest
from fastapi.testclient import TestClient

from src.parking.api.main import app
from src.parking.serve.store import store


@pytest.fixture(scope="module", autouse=True)
def client():
    store.load()
    with TestClient(app) as c:
        yield c


def test_health_endpoint(client):
    r = client.get("/health")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "healthy"
    assert data["ready"] is True
    assert data["meter_count"] > 3000


def test_meta_endpoint(client):
    r = client.get("/v1/meta")
    assert r.status_code == 200
    data = r.json()
    assert "metadata" in data
    assert "data_as_of" in data["metadata"]
    assert "active_meters" in data


def test_spots_endpoint(client):
    r = client.get("/v1/spots?lat=32.7073&lon=-117.1566&radius_m=500&limit=5")
    assert r.status_code == 200
    data = r.json()
    assert "query" in data
    assert "results" in data
    assert len(data["results"]) == 5
    top = data["results"][0]
    assert "pole_id" in top
    assert "lat" in top
    assert "lon" in top
    assert "availability" in top


def test_spots_out_of_bounds(client):
    r = client.get("/v1/spots?lat=40.7128&lon=-74.0060")  # NYC
    assert r.status_code == 400


def test_meter_detail_endpoint(client):
    # Grab a pole_id from spots query
    spots_resp = client.get("/v1/spots?lat=32.7073&lon=-117.1566&limit=1").json()
    pole_id = spots_resp["results"][0]["pole_id"]

    r = client.get(f"/v1/meters/{pole_id}")
    assert r.status_code == 200
    data = r.json()
    assert "meter" in data
    assert data["meter"]["pole_id"] == pole_id
    assert "weekly_profile" in data
    assert len(data["weekly_profile"]) > 0


def test_meter_not_found(client):
    r = client.get("/v1/meters/NON-EXISTENT-POLE-9999")
    assert r.status_code == 404

