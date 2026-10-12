"""Cooperative cancellation for application workers, independent of pipeline pause."""

import threading
from functools import wraps

stop_event = threading.Event()


class WorkerStopped(Exception):
    """Raised at an interruptible wait when the application is shutting down."""


def wait(seconds: float) -> None:
    if stop_event.wait(max(0, seconds)):
        raise WorkerStopped()


def stoppable_worker(fn):
    @wraps(fn)
    def run(*args, **kwargs):
        if stop_event.is_set():
            return
        try:
            return fn(*args, **kwargs)
        except WorkerStopped:
            return

    return run
