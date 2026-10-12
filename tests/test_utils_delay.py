"""utils.delay.parse_retry_after 的解析契约。"""

from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import pytest

from utils.delay import DEFAULT_RETRY_AFTER_SECONDS, parse_retry_after


@pytest.mark.parametrize(
    "raw",
    # 非有限字面量（NaN/Infinity/-Infinity）与负数走同一 `isfinite or < 0` 守卫，
    # 每类取一个代表即可。
    ["NaN", "-1"],
)
def test_invalid_numeric_retry_after_uses_safe_default(raw):
    assert parse_retry_after(raw) == pytest.approx(DEFAULT_RETRY_AFTER_SECONDS)


def test_non_finite_or_negative_retry_defaults_are_also_rejected():
    assert parse_retry_after(None, default=float("nan")) == pytest.approx(
        DEFAULT_RETRY_AFTER_SECONDS
    )
    assert parse_retry_after(None, default=-1) == pytest.approx(
        DEFAULT_RETRY_AFTER_SECONDS
    )


def test_retry_after_accepts_seconds_and_http_date():
    assert parse_retry_after("42") == pytest.approx(42.0)
    assert parse_retry_after(" 7 ") == pytest.approx(7.0)

    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    header = format_datetime(now + timedelta(seconds=90))
    assert parse_retry_after(header, now=now.timestamp()) == pytest.approx(90.0, abs=1.0)

    # 已过期的日期解析为 0，而不是负数或默认值。
    past = format_datetime(now - timedelta(seconds=30))
    assert parse_retry_after(past, now=now.timestamp()) == pytest.approx(0.0)


def test_missing_header_returns_configured_default():
    assert parse_retry_after(None) == pytest.approx(DEFAULT_RETRY_AFTER_SECONDS)
    assert parse_retry_after(None, default=300.0) == pytest.approx(300.0)
