from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient
from google.genai import types

from parking.api.main import app
from parking.geo.geocode import NominatimGeocoder
from parking.llm.gemini_client import ASK, HISTORICAL, OUTSIDE
from parking.llm.tools import Coordinates, ParkingService

POINT = {"lat": 32.7073, "lon": -117.1566}


def call(name, **args):
    return types.Part(function_call=types.FunctionCall(name=name, args=args))


def script(*parts):
    responses = [
        types.GenerateContentResponse(
            candidates=[
                types.Candidate(content=types.Content(role="model", parts=[part]))
            ]
        )
        for part in parts
    ]
    return Mock(side_effect=responses)


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with TestClient(app) as client:
        app.state.parking = Mock()
        app.state.geocoder = Mock()
        yield client


def post(client, text, **kwargs):
    return client.post(
        "/v1/chat", json={"messages": [{"role": "user", "text": text}], **kwargs}
    )


@pytest.mark.parametrize("named,unmetered", [(True, False), (False, True)])
def test_lookup(client, named, unmetered):
    arrival = "2026-11-07T19:00:00-08:00"
    parts = [call("geocode", query="Petco Park")] if named else []
    app.state.geocoder.lookup.return_value = Coordinates(**POINT)
    app.state.generate = script(
        *parts,
        call(
            "find_parking",
            **POINT,
            arrival_time=arrival,
            duration_minutes=120,
            radius_m=9999,
        ),
        types.Part(text="Invented meter FAKE-999 is empty"),
    )
    rows = [
        {
            "pole_id": "TEST-1",
            "status": "unmetered_now" if unmetered else "metered",
            "walk_minutes": 1,
            "distance_m": 80,
            "availability": {"band": "likely", "confidence": "medium"},
        }
    ]

    def spots(request):
        assert request.url.path == "/v1/spots"
        assert dict(request.url.params) == {
            "lat": str(POINT["lat"]),
            "lon": str(POINT["lon"]),
            "arrival_time": arrival,
            "duration_minutes": "120",
            "radius_m": "2000",
            "limit": "10",
        }
        return httpx.Response(200, json={"results": rows, "data_as_of": "2026-11-01"})

    with httpx.Client(
        base_url="http://m3", transport=httpx.MockTransport(spots)
    ) as http:
        app.state.parking = ParkingService(http)
        response = post(
            client,
            "Near Petco Park tonight at 7 for 2 hours"
            if named
            else "Near me at 7 tonight for 2 hours",
            user_location=None if named else POINT,
        )
    assert response.status_code == 200
    body = response.json()
    assert body["results"] == rows and body["data_as_of"] == "2026-11-01"
    assert "FAKE" not in body["reply"] and HISTORICAL in body["reply"]
    assert ("occupancy is unknown" if unmetered else "medium confidence") in body[
        "reply"
    ]


@pytest.mark.parametrize("outside", [False, True])
def test_missing_destination_cannot_search_guessed_coordinates(client, outside):
    app.state.geocoder.lookup.return_value = None
    parts = [call("geocode", query="Los Angeles")] if outside else []
    app.state.generate = script(
        *parts, call("find_parking", **POINT), types.Part(text="FAKE")
    )
    body = post(client, "Parking in Los Angeles" if outside else "Near me").json()
    assert body == {
        "reply": OUTSIDE if outside else ASK,
        "results": [],
        "data_as_of": None,
    }
    app.state.parking.search.assert_not_called()


def test_rate_limit(client):
    app.state.generate = Mock(return_value=types.GenerateContentResponse())
    for _ in range(10):
        assert post(client, "Near me").status_code == 200
    assert post(client, "Near me").status_code == 429
    assert app.state.generate.call_count == 10


@pytest.mark.parametrize("city,found", [("San Diego", True), ("Los Angeles", False)])
def test_geocoder_filters_and_caches(city, found):
    requests = []

    def geocode(request):
        requests.append(request)
        assert request.url.params["q"] == "Petco Park"
        assert request.url.params["format"] == "geocodejson"
        assert request.url.params["bounded"] == "1"
        assert "HackForHumanity" in request.headers["User-Agent"]
        assert "key" not in request.url.params
        return httpx.Response(
            200,
            json={
                "features": [
                    {
                        "properties": {"geocoding": {"city": city}},
                        "geometry": {
                            "type": "Point",
                            "coordinates": [POINT["lon"], POINT["lat"]],
                        },
                    }
                ]
            },
        )

    with httpx.Client(transport=httpx.MockTransport(geocode)) as http:
        geo = NominatimGeocoder(http)
        assert bool(geo.lookup("Petco Park")) == found
        assert bool(geo.lookup("petco park")) == found
    assert len(requests) == 1
