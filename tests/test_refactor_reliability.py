import copy
import threading
from concurrent.futures import ThreadPoolExecutor

from app.strategies import management, registry, repository, simulation
from app.services import lifecycle


def test_concurrent_strategy_saves_preserve_all_records():
    base = registry.get_strategy("system.buy.default")
    barrier = threading.Barrier(8)

    def save(index):
        strategy = copy.deepcopy(base)
        strategy["id"] = f"custom.buy.concurrent{index}"
        barrier.wait(timeout=5)
        return management.save_strategy(strategy)["id"]

    with ThreadPoolExecutor(max_workers=8) as executor:
        saved = set(executor.map(save, range(8)))
    assert {s["id"] for s in repository._strategy_store()["strategies"]} == saved


def test_discount_simulation_uses_submitted_threshold():
    results = []
    for limit in (0.01, 0.99):
        strategy = copy.deepcopy(registry.get_strategy("system.buy.default"))
        for step in strategy["steps"]:
            if step["module_id"] == "guard.max_discount":
                step["params"] = {"max_discount": limit}
        result = simulation.simulate_strategy({"strategy": strategy})
        guard = next(r for r in result["results"] if r["module_id"] == "guard.max_discount")
        assert guard["output"]["limit"] == limit
        results.append(guard["status"])
    assert results == ["reject", "pass"]


def test_unimplemented_simulation_steps_do_not_claim_to_pass():
    result = simulation.simulate_strategy({"strategy_id": "system.buy.default"})
    assert result["mode"] == "sample"
    history = next(r for r in result["results"] if r["module_id"] == "guard.history_data_window")
    assert history["status"] == "sample"


def test_worker_shutdown_interrupts_hour_long_wait(monkeypatch):
    from app.background import exchange_rates
    from app.services.task_queue import TaskQueue

    started = threading.Event()

    def config():
        started.set()
        return {"system": {"exchange_rate_refresh_hours": 0}}

    monkeypatch.setattr(exchange_rates, "load_app_config_validated", config)
    queue = TaskQueue(1)
    task_id = queue.submit(exchange_rates.exchange_rate_worker)
    assert started.wait(timeout=2)
    lifecycle.stop_event.set()
    finished = threading.Event()
    shutdown = threading.Thread(target=lambda: (queue.shutdown(), finished.set()), daemon=True)
    shutdown.start()
    assert finished.wait(timeout=2), "shutdown must interrupt the 3600-second worker wait"
    assert queue.get_task(task_id)["status"] == "success"


def test_atomic_strategy_write_keeps_previous_file_on_failure(monkeypatch, tmp_path):
    from utils import json_store

    path = tmp_path / "strategies.json"
    path.write_text('{"strategies": []}')

    def fail(*args):
        raise OSError("simulated disk error")

    monkeypatch.setattr(json_store.os, "replace", fail)
    import pytest

    with pytest.raises(OSError):
        json_store.atomic_write_json(path, {"strategies": [{"id": "new"}]})
    assert path.read_text() == '{"strategies": []}'
    assert list(tmp_path.glob("*.tmp")) == []


def test_price_cleanup_and_percentile_keep_numeric_semantics(monkeypatch):
    from analysis import stability

    assert stability._percentile([], 50) == 0
    assert stability._percentile([10], 50) == 10
    assert stability._percentile([1, 2, 3, 4], 50) == 2.5
    assert stability.clean_prices_iqr([10, 10, 10, 10, 1000]) == [10, 10, 10, 10]
    monkeypatch.setattr(stability, "_iqr_bounds", lambda _prices: (100, 200))
    assert stability.clean_prices_iqr([1, 2, 3, 4, 5]) == [2, 3, 4]
