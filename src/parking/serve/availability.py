"""Availability Query Engine (Stage 6).

Implements the single find_parking query path used by both GET /v1/spots
and the Gemini find_parking function-calling tool.
"""

import math
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import holidays
from zoneinfo import ZoneInfo

from src.parking.serve.store import store

SAN_DIEGO_TZ = ZoneInfo("America/Los_Angeles")
CA_HOLIDAYS = holidays.US(subdiv="CA")


def parse_arrival_datetime(arrival_time: Optional[str]) -> datetime:
    """Parse arrival time string or return current local time in America/Los_Angeles."""
    if not arrival_time:
        return datetime.now(SAN_DIEGO_TZ)
    if isinstance(arrival_time, datetime):
        if arrival_time.tzinfo is None:
            return arrival_time.replace(tzinfo=SAN_DIEGO_TZ)
        return arrival_time.astimezone(SAN_DIEGO_TZ)

    # Clean string format
    arrival_str = str(arrival_time).strip()
    try:
        # ISO 8601 parsing
        dt = datetime.fromisoformat(arrival_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=SAN_DIEGO_TZ)
        return dt.astimezone(SAN_DIEGO_TZ)
    except Exception:
        pass

    # Try common formats
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            dt = datetime.strptime(arrival_str, fmt)
            return dt.replace(tzinfo=SAN_DIEGO_TZ)
        except ValueError:
            pass

    return datetime.now(SAN_DIEGO_TZ)


def is_enforced(meter: Dict[str, Any], dt: datetime) -> bool:
    """Determine whether a meter is actively enforced at the given datetime."""
    # Check if target date is a California holiday (meters are generally unmetered on holidays)
    if dt.date() in CA_HOLIDAYS:
        return False

    enforced_hours = meter.get("enforced_hours")
    # DuckDB dayofweek: Sun=0, Mon=1, ..., Sat=6
    # Python isoweekday: Mon=1, ..., Sun=7 -> (dt.isoweekday() % 7) gives Sun=0, Mon=1...
    dow = dt.isoweekday() % 7
    minutes_from_midnight = dt.hour * 60 + dt.minute

    if enforced_hours:
        days = enforced_hours.get("days", [1, 2, 3, 4, 5, 6])
        if dow not in days:
            return False
        start_min = enforced_hours.get("start_minutes", 480)  # 8:00 AM
        end_min = enforced_hours.get("end_minutes", 1200)      # 8:00 PM
        return start_min <= minutes_from_midnight < end_min

    # Default fallback: Mon-Sat 8:00 AM - 8:00 PM
    if dow == 0:  # Sunday
        return False
    return 480 <= minutes_from_midnight < 1200


def find_parking(
    lat: float,
    lon: float,
    arrival_time: Optional[str] = None,
    duration_minutes: Optional[int] = None,
    radius_m: int = 400,
    max_results: int = 10,
) -> Dict[str, Any]:
    """Find metered parking spots ranked by probability of open space and proximity."""
    cfg = store.config
    bounds = cfg.get("bounds", {})
    min_lat = bounds.get("min_lat", 32.5)
    max_lat = bounds.get("max_lat", 33.3)
    min_lon = bounds.get("min_lon", -117.6)
    max_lon = bounds.get("max_lon", -116.8)

    # 1. Validate coordinates
    if not (min_lat <= lat <= max_lat and min_lon <= lon <= max_lon):
        raise ValueError(
            f"Coordinates ({lat}, {lon}) are outside San Diego coverage bounding box "
            f"[{min_lat} to {max_lat}, {min_lon} to {max_lon}]."
        )

    search_cfg = cfg.get("search", {})
    max_radius_m = search_cfg.get("max_radius_m", 2000)
    clamped_radius = max(50, min(radius_m, max_radius_m))
    clamped_limit = max(1, min(max_results, 50))
    distance_decay_m = search_cfg.get("distance_decay_m", 250)
    walk_speed = search_cfg.get("walk_speed_m_per_min", 80)

    # 2. Parse arrival time
    dt = parse_arrival_datetime(arrival_time)
    dow = dt.isoweekday() % 7  # 0=Sun, 1=Mon... 6=Sat
    bucket = (dt.hour * 60 + dt.minute) // 15

    # 3. Spatial candidate retrieval
    candidates = store.query_radius(lat, lon, clamped_radius)
    # Filter out meters that are possibly_out_of_service
    active_candidates = [
        (m, dist) for m, dist in candidates
        if m.get("status") != "possibly_out_of_service"
    ]

    pole_ids = [m["pole_id"] for m, _ in active_candidates]
    profiles_map = store.get_profiles_batch(pole_ids, dow, bucket)

    results = []
    notes = [
        "Estimates come from historical meter payments, not live sensors.",
        "Walking times are straight-line approximations."
    ]

    for meter, distance_m in active_candidates:
        pole_id = meter["pole_id"]
        meter_enforced = is_enforced(meter, dt)
        walk_minutes = round(distance_m / walk_speed, 1)

        # Check stay duration vs meter limit
        max_minutes = meter.get("max_minutes")
        limit_exceeded = False
        duration_note = None

        if duration_minutes and max_minutes:
            if duration_minutes > max_minutes:
                limit_exceeded = True
                duration_note = f"Stay duration ({duration_minutes}m) exceeds meter limit ({max_minutes}m)."

        if not meter_enforced:
            # Unmetered off-hours
            p_avail = None
            band = "likely"
            confidence = "medium"
            basis = "enforcement_calendar"
            status = "unmetered_now"
            score = 1.0 * math.exp(-distance_m / distance_decay_m)
        else:
            status = "metered"
            prof = profiles_map.get(pole_id)
            if prof:
                p_avail = round(prof["p_available"], 2)
                band = prof["band"]
                confidence = prof["confidence"]
                basis = prof["basis"]
            else:
                p_avail = 0.50
                band = "possible"
                confidence = "low"
                basis = "default"

            score = p_avail * math.exp(-distance_m / distance_decay_m)

        item = {
            "pole_id": pole_id,
            "lat": meter["lat"],
            "lon": meter["lon"],
            "distance_m": round(distance_m, 1),
            "walk_minutes": walk_minutes,
            "status": status,
            "availability": {
                "p_available": p_avail,
                "band": band,
                "confidence": confidence,
                "basis": basis,
            },
            "meter_type": meter["meter_type"],
            "rate_per_hour": meter.get("rate_per_hour"),
            "max_minutes": max_minutes,
            "limit_exceeded": limit_exceeded,
            "area": meter.get("area"),
            "sub_area": meter.get("sub_area"),
            "_score": score,
        }
        if duration_note:
            item["note"] = duration_note

        results.append(item)

    # Sort descending by score
    results.sort(key=lambda x: x["_score"], reverse=True)
    top_results = results[:clamped_limit]

    # Clean internal score key
    for r in top_results:
        del r["_score"]

    return {
        "query": {
            "lat": lat,
            "lon": lon,
            "arrival_time": dt.isoformat(),
            "duration_minutes": duration_minutes,
            "radius_m": clamped_radius,
            "max_results": clamped_limit,
        },
        "data_as_of": store.metadata.get("data_as_of"),
        "results": top_results,
        "notes": notes,
    }

