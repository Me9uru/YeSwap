"""Tests never read the user's runtime data or contact external services."""

import os
import tempfile

# Set before test collection imports application modules.
_test_data = tempfile.TemporaryDirectory(prefix="aetherswap-tests-")
os.environ["AETHERSWAP_DATA_DIR"] = _test_data.name

import pytest


@pytest.fixture(autouse=True)
def isolated_runtime(monkeypatch, tmp_path):
    from app.strategies import repository as strategy_repository
    from app.storage import connection as db_connection
    import socket
    import config
    from app.accounts import repository as accounts
    from app.configuration import repository as config_loader
    from app.runtime import state
    from app.runtime.pipeline_control import clear_shutdown_pending
    from app.services.lifecycle import stop_event

    stop_event.clear()
    clear_shutdown_pending()
    monkeypatch.setattr(db_connection, "_DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(db_connection, "_engine", None)
    db_connection.init_db()
    monkeypatch.setattr(strategy_repository, "STRATEGY_STORE_PATH", tmp_path / "strategies.json")
    monkeypatch.setattr(strategy_repository, "USER_MODULE_STORE_PATH", tmp_path / "modules.json")
    from app.services import buff_checkout_guard
    from buff import request_policy

    monkeypatch.setattr(config, "_CREDENTIALS_FILE", tmp_path / "credentials.json")
    monkeypatch.setattr(config, "_APP_CONFIG_FILE", tmp_path / "app_config.json")
    monkeypatch.setattr(config, "_cache", {})
    monkeypatch.setattr(accounts, "_ACCOUNTS_FILE", tmp_path / "accounts.json")
    monkeypatch.setattr(accounts, "_cache", None)
    monkeypatch.setattr(buff_checkout_guard, "_GUARD_PATH", tmp_path / "checkout.json")
    monkeypatch.setattr(
        request_policy,
        "_GLOBAL_POLICY",
        request_policy.BuffRequestPolicy(min_interval=0, state_path=None, persist=False),
    )
    config_loader._invalidate_config_cache()
    state.set_state(None)

    def forbidden_network(*args, **kwargs):
        raise AssertionError("External network is disabled in tests; mock the transport")

    monkeypatch.setattr(socket.socket, "connect", forbidden_network)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden_network)
    yield
    if db_connection._engine is not None:
        db_connection._engine.dispose()
    config_loader._invalidate_config_cache()
    state.set_state(None)
    clear_shutdown_pending()


def pytest_sessionfinish(session, exitstatus):
    _test_data.cleanup()
