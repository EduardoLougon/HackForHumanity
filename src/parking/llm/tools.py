"""Tool declarations and dispatch for Gemini function calling."""

import json
from typing import Any, Callable, Dict, List, Optional, Tuple

from src.parking.geo.geocode import geocode_place
from src.parking.serve.availability import find_parking
from src.parking.serve.store import store


def tool_geocode(query: str) -> Dict[str, Any]:
    """Convert a place name or address in San Diego into coordinates."""
    coords = geocode_place(query)
    if not coords:
        return {"error": f"Could not find coordinates for '{query}' in San Diego. Please ask the user to clarify the location or street."}
    return {
        "query": query,
        "lat": coords[0],
        "lon": coords[1],
        "city": "San Diego",
    }


def tool_find_parking(
    lat: float,
    lon: float,
    arrival_time: Optional[str] = None,
    duration_minutes: Optional[int] = None,
    radius_m: int = 400,
    max_results: int = 10,
) -> Dict[str, Any]:
    """Find metered parking spots ranked by estimated likelihood of available space."""
    try:
        raw_res = find_parking(
            lat=lat,
            lon=lon,
            arrival_time=arrival_time,
            duration_minutes=duration_minutes,
            radius_m=radius_m,
            max_results=max_results,
        )
    except ValueError as e:
        return {"error": str(e)}

    # Trim fields for the LLM to save tokens and focus its plain-language response
    compact_results = []
    for item in raw_res["results"][:5]:  # Send top 5 to Gemini
        avail = item.get("availability", {})
        compact = {
            "pole_id": item["pole_id"],
            "distance_m": item["distance_m"],
            "walk_minutes": item["walk_minutes"],
            "status": item["status"],
            "band": avail.get("band"),
            "confidence": avail.get("confidence"),
            "p_available": avail.get("p_available"),
            "meter_type": item.get("meter_type"),
            "rate_per_hour": item.get("rate_per_hour"),
            "max_minutes": item.get("max_minutes"),
            "limit_exceeded": item.get("limit_exceeded", False),
            "area": item.get("area"),
            "sub_area": item.get("sub_area"),
        }
        if "note" in item:
            compact["note"] = item["note"]
        compact_results.append(compact)

    return {
        "status": "success",
        "arrival_time": raw_res["query"]["arrival_time"],
        "count": len(raw_res["results"]),
        "top_spots": compact_results,
        "notes": raw_res.get("notes", []),
        "_raw_response": raw_res,  # Kept in Python memory for the final API response
    }


def tool_get_meter_details(pole_id: str) -> Dict[str, Any]:
    """Get rate, time limit, and availability patterns for a specific meter pole."""
    details = store.get_meter_details(pole_id)
    if not details:
        return {"error": f"Meter pole '{pole_id}' not found."}

    meter = details["meter"]
    return {
        "pole_id": meter["pole_id"],
        "area": meter.get("area"),
        "sub_area": meter.get("sub_area"),
        "meter_type": meter.get("meter_type"),
        "rate_per_hour": meter.get("rate_per_hour"),
        "max_minutes": meter.get("max_minutes"),
        "enforced_hours": meter.get("enforced_hours"),
        "status": meter.get("status"),
    }


TOOL_DEFINITIONS = [
    {
        "name": "geocode",
        "description": "Convert a place name, landmark, or street address in San Diego into geographic coordinates (lat, lon).",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "query": {
                    "type": "STRING",
                    "description": "The landmark, venue, or address in San Diego to locate (e.g., 'Petco Park', 'Little Italy')."
                }
            },
            "required": ["query"],
        },
    },
    {
        "name": "find_parking",
        "description": "Find metered street parking near coordinates in San Diego, ranked by estimated probability of open space at arrival time and walking distance.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "lat": {"type": "NUMBER", "description": "Target latitude."},
                "lon": {"type": "NUMBER", "description": "Target longitude."},
                "arrival_time": {
                    "type": "STRING",
                    "description": "Target arrival time formatted as an ISO 8601 string with America/Los_Angeles offset (e.g. '2026-11-08T19:00:00-08:00'). Omit if user wants now."
                },
                "duration_minutes": {
                    "type": "INTEGER",
                    "description": "Expected stay duration in minutes (e.g. 120 for 2 hours)."
                },
                "radius_m": {
                    "type": "INTEGER",
                    "description": "Search radius in meters (default 400, max 2000)."
                },
                "max_results": {
                    "type": "INTEGER",
                    "description": "Maximum number of spots to retrieve (default 10)."
                },
            },
            "required": ["lat", "lon"],
        },
    },
    {
        "name": "get_meter_details",
        "description": "Get rate, maximum stay limit, and enforcement hours for a specific meter pole ID.",
        "parameters": {
            "type": "OBJECT",
            "properties": {
                "pole_id": {
                    "type": "STRING",
                    "description": "The unique meter pole ID (e.g. '1-1004')."
                }
            },
            "required": ["pole_id"],
        },
    },
]

TOOL_REGISTRY: Dict[str, Callable[..., Dict[str, Any]]] = {
    "geocode": tool_geocode,
    "find_parking": tool_find_parking,
    "get_meter_details": tool_get_meter_details,
}

