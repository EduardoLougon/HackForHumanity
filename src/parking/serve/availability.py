from datetime import datetime
from functools import lru_cache
from math import exp
from zoneinfo import ZoneInfo

import holidays

from parking.serve.store import AVAILABILITY_FIELDS

LOCAL = ZoneInfo("America/Los_Angeles")


@lru_cache(maxsize=16)
def holiday_dates(year):
    return holidays.US(subdiv="CA", years=year)


def find_parking(store, query):
    arrival = (query.arrival_time or datetime.now(LOCAL)).astimezone(LOCAL)
    dow = arrival.weekday()
    minute = arrival.hour * 60 + arrival.minute
    bucket = minute // store.bucket_minutes
    holiday = arrival.date() in holiday_dates(arrival.year)
    radius = min(2000, max(50, query.radius_m))
    ranked = []
    for meter, distance in store.nearby(query.lat, query.lon, radius):
        maximum = meter["max_minutes"]
        if meter["status"] == "possibly_out_of_service":
            continue
        if (
            maximum is not None
            and query.duration_minutes
            and query.duration_minutes > maximum
        ):
            continue
        profile = store.profile(meter["pole_id"], dow, bucket)
        hours = meter["enforced_hours_json"]
        # A missing schedule or profile alone does not establish free parking.
        off_hours = hours is not None and not any(
            start <= minute < end for start, end in hours.get(str(dow), [])
        )
        if holiday or off_hours:
            status, profile = "unmetered_now", None
        else:
            status = "metered" if profile or hours is not None else "unknown"
        estimate = profile or dict.fromkeys(AVAILABILITY_FIELDS)
        probability = estimate["p_available"]
        score = -1 if probability is None else float(probability) * exp(-distance / 250)
        result = {
            key: meter[key]
            for key in (
                "pole_id",
                "lat",
                "lon",
                "meter_type",
                "rate_per_hour",
                "max_minutes",
                "area",
                "sub_area",
            )
        }
        result.update(
            distance_m=round(distance, 1),
            walk_minutes=round(distance / 80, 1),
            status=status,
            availability=estimate,
        )
        ranked.append((-score, distance, meter["pole_id"], result))
    results = [item[3] for item in sorted(ranked)[: query.max_results]]
    notes = [
        "Estimates come from historical meter payments, not live sensors. Walking times are approximate."
    ]
    if query.duration_minutes and any(row["max_minutes"] is None for row in results):
        notes.append(
            "Some meters have an unknown time limit. Check posted signs for your stay."
        )
    return {
        "query": {
            "lat": query.lat,
            "lon": query.lon,
            "arrival_time": arrival.isoformat(),
            "radius_m": radius,
            "duration_minutes": query.duration_minutes,
        },
        "data_as_of": store.metadata["data_as_of"],
        "results": results,
        "notes": notes,
    }
