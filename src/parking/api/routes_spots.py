"""FastAPI Routes for Spots & Meter Information."""

from typing import Any, Dict, Optional
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from src.parking.serve.availability import find_parking
from src.parking.serve.store import store

router = APIRouter()


@router.get("/health")
def get_health() -> Dict[str, Any]:
    """Health and readiness probe."""
    is_ready = store.con is not None and bool(store.meters_by_id)
    return {
        "status": "healthy" if is_ready else "initializing",
        "ready": is_ready,
        "database_connected": store.con is not None,
        "meter_count": len(store.meters_by_id),
    }


@router.get("/v1/meta")
def get_metadata() -> Dict[str, Any]:
    """Return pipeline freshness metadata and dataset bounds."""
    store.reload_if_changed()
    if not store.metadata:
        raise HTTPException(status_code=503, detail="Store metadata not loaded.")
    return {
        "metadata": store.metadata,
        "active_meters": len(store.meters_by_id),
        "bounds": store.config.get("bounds"),
        "search_defaults": store.config.get("search"),
    }


@router.get("/v1/spots")
def get_spots(
    lat: float = Query(..., description="Target latitude (e.g. 32.7073 for Petco Park)"),
    lon: float = Query(..., description="Target longitude (e.g. -117.1566)"),
    arrival_time: Optional[str] = Query(None, description="ISO 8601 arrival time in America/Los_Angeles (defaults to now)"),
    duration_minutes: Optional[int] = Query(None, ge=1, le=1440, description="Intended stay duration in minutes"),
    radius_m: int = Query(400, ge=50, le=2000, description="Search radius in meters (50 to 2000)"),
    limit: int = Query(10, ge=1, le=50, description="Maximum number of spots to return"),
) -> Dict[str, Any]:
    """Deterministic geospatial search ranking meters by probability of available space."""
    try:
        return find_parking(
            lat=lat,
            lon=lon,
            arrival_time=arrival_time,
            duration_minutes=duration_minutes,
            radius_m=radius_m,
            max_results=limit,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Internal error querying availability: {e}")


@router.get("/v1/meters/{pole_id}")
def get_meter(pole_id: str) -> Dict[str, Any]:
    """Return detail view and weekly profile for a single parking meter pole."""
    details = store.get_meter_details(pole_id)
    if not details:
        raise HTTPException(status_code=404, detail=f"Meter pole '{pole_id}' not found.")
    return details

