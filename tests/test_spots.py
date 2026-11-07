import duckdb
import pytest
from fastapi.testclient import TestClient

from parking.api.main import app
from parking.llm.tools import Search
from parking.transform.build_serving import build

PARAMS = {"lat": 32.71, "lon": -117.16, "arrival_time": "2026-11-09T18:15:00Z"}


def make_db(directory, size=0):
    with duckdb.connect() as db:
        db.execute("""CREATE TABLE meters AS SELECT pole_id, lat, -117.16::DOUBLE lon, 'Z' AS "zone",
            'A' area, 'S' sub_area, 'single_space' meter_type, 2.5 rate_per_hour, max_minutes,
            hours enforced_hours_json, status FROM (VALUES
            ('near',32.7101,120,'{"0":[[540,1020]]}','active'),
            ('best',32.7103,NULL,NULL,'active'),
            ('short',32.7100,20,NULL,'active'),
            ('inactive',32.7100,120,NULL,'possibly_out_of_service'),
            ('unknown',32.7102,NULL,NULL,'active'),
            ('far',32.81,120,NULL,'active')) t(pole_id,lat,max_minutes,hours,status)""")
        if size:
            db.execute(
                """INSERT INTO meters SELECT 'grid-' || i, 32.6 + (i % 100)*0.002,
                -117.25 + (i // 100)*0.002,'Z','A','S','single_space',2,120,NULL,'active'
                FROM range(?) t(i)""",
                [size],
            )
        db.execute("""CREATE TABLE profiles AS SELECT pole_id, 0 dow, 41 bucket,
            CASE WHEN pole_id='near' THEN 0.31 ELSE 0.9 END p_available,
            CASE WHEN pole_id='near' THEN 'possible' ELSE 'likely' END band,
            'high' confidence, 'pole' basis, 14 n_dates FROM meters WHERE pole_id!='unknown'""")
        db.execute("""CREATE TABLE metadata AS SELECT '2026-11-01T10:00:00-08:00' data_as_of,
            '2026-11-02' built_at, '2026-05-01' profile_window_start, '2026-11-01' profile_window_end,
            'fixture' pipeline_version""")
        for table in ("meters", "profiles", "metadata"):
            db.table(table).write_parquet(str(directory / (table + ".parquet")))
    output = directory / "parking.duckdb"
    build(
        *(
            directory / (name + ".parquet")
            for name in ("meters", "profiles", "metadata")
        ),
        output,
    )
    return output


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("PARKING_DB_PATH", str(make_db(tmp_path)))
    with TestClient(app) as client:
        yield client


def test_search(client):
    params = dict(PARAMS, duration_minutes=60, radius_m=1)
    data = client.get("/v1/spots", params=params).json()
    assert data["query"]["arrival_time"] == "2026-11-09T10:15:00-08:00"
    assert data["query"]["radius_m"] == 50
    assert [r["pole_id"] for r in data["results"]] == ["best", "near", "unknown"]
    assert data["results"][0]["availability"]["p_available"] == 0.9
    assert data["results"][0]["distance_m"] == pytest.approx(33.4, abs=0.2)
    assert data["results"][0]["walk_minutes"] == 0.4
    assert data["results"][-1]["status"] == "unknown"
    assert any("time limit" in note for note in data["notes"])
    assert app.state.parking.search(Search(**params)) == data
    assert (
        len(client.get("/v1/spots", params=dict(params, limit=1)).json()["results"])
        == 1
    )


@pytest.mark.parametrize(
    "when", ["2026-11-09T18:00:00-08:00", "2026-11-26T10:15:00-08:00"]
)
def test_unmetered(client, when):
    data = client.get("/v1/spots", params=dict(PARAMS, arrival_time=when)).json()
    near = next(r for r in data["results"] if r["pole_id"] == "near")
    assert near["status"] == "unmetered_now"
    assert near["availability"]["p_available"] is None


def test_routes(client):
    assert client.get("/health").json()["serving_db_loaded"]
    assert client.get("/v1/meta").json()["meter_count"] == 6
    assert client.get("/v1/meters/near").json()["weekly_profile"][0]["bucket"] == 41
    assert client.get("/v1/meters/absent").status_code == 404
    assert client.get("/v1/spots", params=dict(PARAMS, lat=0)).status_code == 422
    assert (
        client.get(
            "/v1/spots", params=dict(PARAMS, arrival_time="2026-11-09T10:00")
        ).status_code
        == 422
    )


def test_no_database(tmp_path, monkeypatch):
    monkeypatch.setenv("PARKING_DB_PATH", str(tmp_path / "absent"))
    with TestClient(app) as client:
        assert not client.get("/health").json()["serving_db_loaded"]
        assert client.get("/v1/spots", params=PARAMS).status_code == 503


def test_chat_returns_same_estimates_as_map(client):
    from google.genai import types

    def generate(contents, config):
        if contents[-1].parts[0].function_response:
            part = types.Part(text="Here are your results.")
        else:
            part = types.Part(
                function_call=types.FunctionCall(name="find_parking", args=PARAMS)
            )
        return types.GenerateContentResponse(
            candidates=[
                types.Candidate(content=types.Content(role="model", parts=[part]))
            ]
        )

    app.state.generate = generate
    response = client.post(
        "/v1/chat",
        json={
            "messages": [{"role": "user", "text": "Parking near me Monday at 10:15"}],
            "user_location": {"lat": PARAMS["lat"], "lon": PARAMS["lon"]},
        },
    )
    assert response.status_code == 200
    map_data = client.get("/v1/spots", params=PARAMS).json()
    assert response.json()["results"] == map_data["results"]
    assert response.json()["data_as_of"] == map_data["data_as_of"]
