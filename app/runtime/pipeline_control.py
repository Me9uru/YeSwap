"""购买工作流和维护操作共用的进程内停止状态。"""

import threading


_shutdown_pending = threading.Event()


def is_shutdown_pending() -> bool:
    return _shutdown_pending.is_set()


def mark_shutdown_pending() -> None:
    _shutdown_pending.set()


def clear_shutdown_pending() -> None:
    """Reset process state between isolated test cases."""
    _shutdown_pending.clear()
