import math
import random
import time
from datetime import timezone
from email.utils import parsedate_to_datetime
from typing import Optional

DEFAULT_RETRY_AFTER_SECONDS = 60.0


def parse_retry_after(
    value: Optional[str],
    *,
    now: Optional[float] = None,
    default: float = DEFAULT_RETRY_AFTER_SECONDS,
) -> float:
    """解析 Retry-After 头：支持秒数或 RFC 兼容的 HTTP 日期。

    无法识别、数值非法或调用方传入的默认值本身不合理时，回退到
    ``DEFAULT_RETRY_AFTER_SECONDS``；BUFF 等调用方可通过 ``default``
    保持各自的退避常量。
    """
    fallback = default
    try:
        if not math.isfinite(float(fallback)) or float(fallback) < 0:
            fallback = DEFAULT_RETRY_AFTER_SECONDS
    except (TypeError, ValueError):
        fallback = DEFAULT_RETRY_AFTER_SECONDS
    if value is None:
        return fallback
    raw = str(value).strip()
    try:
        seconds = float(raw)
    except (TypeError, ValueError):
        pass
    else:
        if not math.isfinite(seconds) or seconds < 0:
            return fallback
        return seconds
    try:
        parsed = parsedate_to_datetime(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        current = time.time() if now is None else float(now)
        remaining = parsed.timestamp() - current
        if not math.isfinite(current) or not math.isfinite(remaining):
            return fallback
        return max(0.0, remaining)
    except (TypeError, ValueError, OverflowError):
        return fallback


def jittered_sleep(base: float, jitter_ratio: float = 0.3, *, is_cancelled=None) -> float:
    actual = base * (1 + random.uniform(-jitter_ratio, jitter_ratio))
    actual = max(0.1, actual)
    start_time = time.time()
    while time.time() - start_time < actual:
        if is_cancelled and is_cancelled():
            break
        sleep_time = min(0.5, max(0.0, actual - (time.time() - start_time)))
        if sleep_time <= 0:
            break
        time.sleep(sleep_time)
    return actual
