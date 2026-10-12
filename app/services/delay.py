"""Application-aware delays; low-level transports use utils.delay directly."""

from utils.delay import jittered_sleep as sleep_with_jitter
from app.runtime.state import is_stop_requested
from app.services.lifecycle import stop_event


def jittered_sleep(base: float, jitter_ratio: float = 0.3) -> float:
    return sleep_with_jitter(
        base, jitter_ratio, is_cancelled=lambda: stop_event.is_set() or is_stop_requested()
    )
