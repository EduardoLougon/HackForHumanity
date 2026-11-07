from typing import Annotated
from urllib.parse import quote

import httpx
from google.genai import types
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Coordinates(Arguments):
    lat: float = Field(ge=32.5, le=33.3)
    lon: float = Field(ge=-117.6, le=-116.8)


class Search(Coordinates):
    arrival_time: AwareDatetime | None = None
    duration_minutes: int | None = Field(default=None, gt=0)
    radius_m: int = 400
    max_results: int = Field(default=10, ge=1, le=50)


class Place(Arguments):
    query: Annotated[str, Field(min_length=1, max_length=300)]


class Meter(Arguments):
    pole_id: Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[^/\\?#]+$")]


TOOLS = {
    "geocode": (Place, "Resolve a named San Diego destination to coordinates."),
    "find_parking": (
        Search,
        "Search at a resolved destination or user location. Use ISO time with offset.",
    ),
    "get_meter_details": (
        Meter,
        "Retrieve details for a meter identified by its pole ID.",
    ),
}


def declarations():
    return [
        types.FunctionDeclaration(
            name=name,
            description=description,
            parameters_json_schema=model.model_json_schema(),
        )
        for name, (model, description) in TOOLS.items()
    ]


class ParkingService:
    """Use the M3 HTTP contract so map and chat always share ranking logic."""

    def __init__(self, client: httpx.Client):
        self.client = client

    def search(self, query: Search):
        params = query.model_dump(mode="json", exclude_none=True)
        params["limit"] = params.pop("max_results")
        params["radius_m"] = min(2000, max(50, query.radius_m))
        return self.client.get("v1/spots", params=params).raise_for_status().json()

    def details(self, meter: Meter):
        if meter.pole_id in (".", ".."):
            raise ValueError("Invalid meter ID")
        path = "v1/meters/" + quote(meter.pole_id, safe="")
        response = self.client.get(path)
        if response.status_code == 404:
            raise ValueError("Unknown meter ID")
        return response.raise_for_status().json()
