"""Meter Configuration and Rules Parser.

Extracts rate_per_hour, max_minutes, enforced_days, and enforced_hours
from structured metadata columns and/or free-text config_name strings.
Never guesses — returns None for unparseable fields.
"""

import re
from typing import Any, Dict, List, Optional


DAYS_MAP = {
    "mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6
}
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def parse_time_to_minutes(time_str: str) -> Optional[int]:
    """Convert time string like '8am', '8:30am', '8pm', '10pm' to minutes since midnight."""
    if not time_str or not isinstance(time_str, str):
        return None
    time_str = time_str.strip().lower()
    m = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(am|pm)$", time_str)
    if not m:
        return None
    hours = int(m.group(1))
    minutes = int(m.group(2) or 0)
    meridiem = m.group(3)

    if meridiem == "pm" and hours != 12:
        hours += 12
    elif meridiem == "am" and hours == 12:
        hours = 0
    return hours * 60 + minutes


def minutes_to_hhmm(minutes: int) -> str:
    """Format minutes from midnight to HH:MM string."""
    h = minutes // 60
    m = minutes % 60
    return f"{h:02d}:{m:02d}"


def parse_rate_per_hour(price_str: Optional[str], config_name: Optional[str] = None) -> Optional[float]:
    """Parse hourly rate from price string or config_name (e.g. '$2.50 HR' -> 2.50)."""
    text = price_str or ""
    m = re.search(r"\$(\d+(?:\.\d{1,2})?)\s*(?:/?\s*hr|hour)", text, re.IGNORECASE)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass

    if config_name:
        m2 = re.search(r"\$(\d+(?:\.\d{1,2})?)\s*(?:/?\s*hr|hour)", config_name, re.IGNORECASE)
        if m2:
            try:
                return float(m2.group(1))
            except ValueError:
                pass

    return None


def parse_max_minutes(time_limit_str: Optional[str], config_name: Optional[str] = None) -> Optional[int]:
    """Parse maximum parking duration in minutes (e.g. '2 hour' -> 120, '15 Min Max' -> 15)."""
    candidates = [time_limit_str or "", config_name or ""]
    for text in candidates:
        if not text:
            continue
        # Check for explicit phrases with "max" or "limit" first (e.g. "15 Min Max", "2 Hour Max")
        m_explicit_min = re.search(r"\b(\d+)\s*(?:min|minute|mins)\s*(?:max|limit)?\b", text, re.IGNORECASE)
        m_explicit_hr = re.search(r"(?<!\$)\b(\d+(?:\.\d+)?)\s*(?:hr|hour|hrs)\s*(?:max|limit)?\b", text, re.IGNORECASE)

        # If both or either matched, prefer the one explicitly paired with 'max'
        m_max_min = re.search(r"\b(\d+)\s*(?:min|minute|mins)\s*max\b", text, re.IGNORECASE)
        m_max_hr = re.search(r"(?<!\$)\b(\d+(?:\.\d+)?)\s*(?:hr|hour|hrs)\s*max\b", text, re.IGNORECASE)

        if m_max_min:
            return int(m_max_min.group(1))
        if m_max_hr:
            return int(float(m_max_hr.group(1)) * 60)

        # General matching (excluding dollar rates like $1.25 HR)
        if m_explicit_min and "min" in text.lower() and not m_explicit_hr:
            return int(m_explicit_min.group(1))
        if m_explicit_hr:
            return int(float(m_explicit_hr.group(1)) * 60)
        if m_explicit_min:
            return int(m_explicit_min.group(1))

    return None


def parse_days_range(days_str: str) -> Optional[List[int]]:
    """Convert days string like 'Mon-Sat', 'Mon-Sun', 'Mon-Fri' to list of day-of-week ints (0=Mon, 6=Sun)."""
    if not days_str or not isinstance(days_str, str):
        return None
    days_str = days_str.strip()

    m = re.match(r"^(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s*-\s*(Mon|Tue|Wed|Thu|Fri|Sat|Sun)$", days_str, re.IGNORECASE)
    if m:
        start_d = DAYS_MAP[m.group(1).lower()[:3]]
        end_d = DAYS_MAP[m.group(2).lower()[:3]]
        if start_d <= end_d:
            return list(range(start_d, end_d + 1))
        else:
            return list(range(start_d, 7)) + list(range(0, end_d + 1))

    # Single days or commas
    parts = [p.strip().lower()[:3] for p in re.split(r"[,/ ]+", days_str) if p.strip()]
    if all(p in DAYS_MAP for p in parts) and len(parts) > 0:
        return sorted(list(set(DAYS_MAP[p] for p in parts)))

    return None


def parse_enforced_hours(
    time_start: Optional[str],
    time_end: Optional[str],
    days_in_operation: Optional[str],
    config_name: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Parse active enforcement schedule into structured JSON-compatible dict."""
    start_min = parse_time_to_minutes(time_start) if time_start else None
    end_min = parse_time_to_minutes(time_end) if time_end else None
    days = parse_days_range(days_in_operation) if days_in_operation else None

    # Fallback to config_name regex if structured fields are missing
    if (start_min is None or end_min is None or days is None) and config_name:
        # e.g. 8am-8pm, Mon-Sat
        m_time = re.search(r"(\d{1,2}(?::\d{2})?\s*(?:am|pm))\s*-\s*(\d{1,2}(?::\d{2})?\s*(?:am|pm))", config_name, re.IGNORECASE)
        if m_time:
            if start_min is None:
                start_min = parse_time_to_minutes(m_time.group(1))
            if end_min is None:
                end_min = parse_time_to_minutes(m_time.group(2))

        m_days = re.search(r"(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s*-\s*(Mon|Tue|Wed|Thu|Fri|Sat|Sun)", config_name, re.IGNORECASE)
        if m_days and days is None:
            days = parse_days_range(m_days.group(0))

    if start_min is not None and end_min is not None and days is not None:
        return {
            "days": days,  # list of 0-6 ints
            "day_names": [DAY_NAMES[d] for d in days],
            "start_minutes": start_min,
            "end_minutes": end_min,
            "start_time": minutes_to_hhmm(start_min),
            "end_time": minutes_to_hhmm(end_min),
        }
    return None


def normalize_meter_type(multi_space: Optional[str], config_name: Optional[str] = None) -> str:
    """Normalize meter type to 'single_space', 'multi_space', or 'unknown'."""
    if multi_space is not None and str(multi_space).strip().upper() == "Y":
        return "multi_space"
    if config_name:
        cf = config_name.lower()
        if "multi" in cf or "pay station" in cf or "ms" in cf.split():
            return "multi_space"
    return "single_space"

