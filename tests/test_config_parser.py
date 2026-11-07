"""Unit tests for config_parser.py."""

from src.parking.transform.config_parser import (
    normalize_meter_type,
    parse_days_range,
    parse_enforced_hours,
    parse_max_minutes,
    parse_rate_per_hour,
    parse_time_to_minutes,
)


def test_parse_time_to_minutes():
    assert parse_time_to_minutes("8am") == 8 * 60
    assert parse_time_to_minutes("8:30am") == 8 * 60 + 30
    assert parse_time_to_minutes("12pm") == 12 * 60
    assert parse_time_to_minutes("8pm") == 20 * 60
    assert parse_time_to_minutes("10pm") == 22 * 60
    assert parse_time_to_minutes("12am") == 0
    assert parse_time_to_minutes("invalid") is None
    assert parse_time_to_minutes(None) is None


def test_parse_rate_per_hour():
    assert parse_rate_per_hour("$2.50 HR") == 2.50
    assert parse_rate_per_hour("$1.25 HR") == 1.25
    assert parse_rate_per_hour("$2.50 HR; $10.00 DAY") == 2.50
    assert parse_rate_per_hour(None, "Downtown MK5 - 2 Hour Max $2.50 HR, 8am-8pm, Mon-Sat") == 2.50
    assert parse_rate_per_hour("Free") is None
    assert parse_rate_per_hour(None, None) is None


def test_parse_max_minutes():
    assert parse_max_minutes("2 hour") == 120
    assert parse_max_minutes("4 hour") == 240
    assert parse_max_minutes("9 hour") == 540
    assert parse_max_minutes("1 hour") == 60
    assert parse_max_minutes("30 min") == 30
    assert parse_max_minutes(None, "15 Min Max $1.25 HR 8am-6pm Mon-Sat") == 15
    assert parse_max_minutes(None, "Downtown MK5 - 2 Hour Max $2.50 HR") == 120
    assert parse_max_minutes("No Limit") is None


def test_parse_days_range():
    assert parse_days_range("Mon-Sat") == [0, 1, 2, 3, 4, 5]
    assert parse_days_range("Mon-Sun") == [0, 1, 2, 3, 4, 5, 6]
    assert parse_days_range("Mon-Fri") == [0, 1, 2, 3, 4]
    assert parse_days_range("Invalid") is None


def test_parse_enforced_hours():
    res = parse_enforced_hours("8am", "8pm", "Mon-Sat")
    assert res is not None
    assert res["days"] == [0, 1, 2, 3, 4, 5]
    assert res["start_minutes"] == 480
    assert res["end_minutes"] == 1200
    assert res["start_time"] == "08:00"
    assert res["end_time"] == "20:00"

    # Fallback to config_name
    res2 = parse_enforced_hours(None, None, None, "Downtown MK5 - 2 Hour Max $2.50 HR, 8am-8pm, Mon-Sat, Prepay")
    assert res2 is not None
    assert res2["days"] == [0, 1, 2, 3, 4, 5]
    assert res2["start_time"] == "08:00"
    assert res2["end_time"] == "20:00"


def test_normalize_meter_type():
    assert normalize_meter_type("Y") == "multi_space"
    assert normalize_meter_type(None) == "single_space"
    assert normalize_meter_type("", "Pay Station") == "multi_space"
    assert normalize_meter_type("", "Single Space Meter") == "single_space"

