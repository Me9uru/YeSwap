from datetime import timezone

import pytest

from utils.time import (
    parse_steam_history_date,
    resolve_configured_timezone,
    timestamp_in_configured_timezone,
)


def test_steam_history_parser_preserves_instant_and_returns_utc():
    parsed = parse_steam_history_date("Jul 09 2026 12: +8")

    assert parsed is not None
    assert parsed.tzinfo is timezone.utc
    assert parsed.isoformat() == "2026-07-09T04:00:00+00:00"


@pytest.mark.parametrize(
    "raw",
    [
        "Jul 09 2026 04: +0",
        "Jul 09 2026 04",
    ],
)
def test_steam_history_utc_variants_are_timezone_aware(raw):
    parsed = parse_steam_history_date(raw)

    assert parsed is not None
    assert parsed.tzinfo is timezone.utc
    assert parsed.isoformat() == "2026-07-09T04:00:00+00:00"


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "not a date",
        "Jul 09 2026 04: +1500",
    ],
)
def test_invalid_steam_history_dates_are_rejected(raw):
    assert parse_steam_history_date(raw) is None


def test_epoch_formatting_uses_configured_timezone_not_host_timezone():
    configured_timezone, label = resolve_configured_timezone(
        {
            "timezone": "Asia/Shanghai",
            "timezone_offset_minutes": 480,
        }
    )

    rendered = timestamp_in_configured_timezone(
        1783570563,
        configured_timezone,
    )

    assert label == "Asia/Shanghai"
    assert rendered.strftime("%Y-%m-%d %H:%M:%S") == "2026-07-09 12:16:03"
