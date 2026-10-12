import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_background_workers_are_all_submitted(monkeypatch):
    from app import api

    submitted = []

    class FakeQueue:
        def submit(self, fn, *args, name="", max_retries=0, retry_base_delay=0, **kwargs):
            submitted.append(name or fn.__name__)
            return name or fn.__name__

    monkeypatch.setattr(api, "_bg_started", False)
    monkeypatch.setattr(api, "get_task_queue", lambda: FakeQueue())

    api._start_background_workers()

    assert {
        "sync_account_region",
        "receive_worker",
        "listing_check_worker",
        "exchange_rate_worker",
        "session_keepalive_worker",
    } <= set(submitted)
