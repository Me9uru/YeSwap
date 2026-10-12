from datetime import datetime, timezone

from app.workflows import schedule as pipeline
from app.configuration.schema import DEFAULTS, validate_and_fill


def test_offset_fallback_works_when_iana_timezone_is_invalid():
    schedule_timezone, label = pipeline._resolve_schedule_timezone(
        {
            "timezone": "Invalid/Timezone",
            "timezone_offset_minutes": 480,
        }
    )
    utc_clock = datetime(2026, 7, 9, 4, 16, tzinfo=timezone.utc)
    user_clock = utc_clock.astimezone(schedule_timezone)

    assert label == "UTC+08:00"
    assert user_clock.hour == 12
    assert pipeline._is_in_time_window(8, 22, user_clock) is True

    # 偏移量越界时回退到服务器本地时钟（不应用任何时区）
    local_timezone, _local_label = pipeline._resolve_schedule_timezone(
        {
            "timezone": "",
            "timezone_offset_minutes": 2000,
        }
    )
    assert local_timezone is None


def test_daytime_window_is_start_inclusive_and_end_exclusive():
    assert pipeline._is_in_time_window(
        8, 22, datetime(2026, 7, 9, 8, 0)
    )
    assert pipeline._is_in_time_window(
        8, 22, datetime(2026, 7, 9, 21, 59)
    )
    assert not pipeline._is_in_time_window(
        8, 22, datetime(2026, 7, 9, 7, 59)
    )
    assert not pipeline._is_in_time_window(
        8, 22, datetime(2026, 7, 9, 22, 0)
    )


def test_overnight_and_equal_windows_have_explicit_semantics():
    assert pipeline._is_in_time_window(
        22, 8, datetime(2026, 7, 9, 23, 0)
    )
    assert pipeline._is_in_time_window(
        22, 8, datetime(2026, 7, 9, 7, 0)
    )
    assert not pipeline._is_in_time_window(
        22, 8, datetime(2026, 7, 9, 12, 0)
    )
    assert pipeline._is_in_time_window(
        8, 8, datetime(2026, 7, 9, 3, 0)
    )


def test_runtime_config_coerces_string_boolean_and_invalid_hours():
    validated = validate_and_fill(
        {
            "pipeline": {
                "start_time_limit_enabled": "false",
                "start_time_hour": None,
                "end_time_hour": "21",
            }
        }
    )

    assert validated["pipeline"]["start_time_limit_enabled"] is False
    assert validated["pipeline"]["start_time_hour"] == (
        DEFAULTS["pipeline"]["start_time_hour"]
    )
    assert validated["pipeline"]["end_time_hour"] == 21
