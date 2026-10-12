import sys
import copy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def test_config_route_persists_all_settings_checkboxes(monkeypatch):
    from app.routes import config

    state = {
        "pipeline": {"max_discount": 0.8},
        "steam_confirm": {},
        "system": {},
    }

    def fake_update(patch):
        from app.configuration.schema import merge

        updated = merge(state, patch)
        state.clear()
        state.update(copy.deepcopy(updated))
        return copy.deepcopy(state)

    monkeypatch.setattr(config, "update_app_config_validated", fake_update)

    result = config.api_save_config(
        config.ConfigBody(
            config={
                "pipeline": {"start_time_limit_enabled": True},
                "steam_confirm": {"enabled": True},
                "system": {"buff_session_keepalive_enabled": True},
            }
        )
    )

    assert result["ok"] is True
    assert result["config"]["pipeline"]["max_discount"] == 0.8
    assert result["config"]["pipeline"]["start_time_limit_enabled"] is True
    assert result["config"]["steam_confirm"]["enabled"] is True
    assert result["config"]["system"]["buff_session_keepalive_enabled"] is True


def test_config_get_disables_http_caching(monkeypatch):
    from fastapi import Response
    from app.routes import config

    monkeypatch.setattr(config, "load_app_config_validated", lambda: {})
    response = Response()

    assert config.api_get_config(response) == {"config": {}}
    assert response.headers["cache-control"] == "no-store"


def test_backup_export_omits_removed_feature_settings(monkeypatch):
    from app.services import backup

    monkeypatch.setattr(
        backup,
        "load_app_config",
        lambda: {
            "pipeline": {"max_discount": 0.8},
            "steam_deals": {"enabled": True},
            "notify": {
                "pushplus_token": "secret",
                "holdings_report_interval_hours": 6,
                "email_user": "user@example.com",
            },
        },
    )

    exported = backup.api_export_full()

    assert exported["app_config"] == {
        "pipeline": {"max_discount": 0.8},
        "notify": {"email_user": "user@example.com"},
    }


def test_full_import_uses_validated_config_save(monkeypatch):
    from app.services import backup as config

    calls = {
        "validated": None,
        "credentials": 0,
        "transactions": 0,
        "accounts": 0,
        "log": 0,
    }
    monkeypatch.setattr(config, "save_app_config_validated", lambda data: calls.__setitem__("validated", data))
    monkeypatch.setattr(config, "save_credentials", lambda data: calls.__setitem__("credentials", calls["credentials"] + 1))
    monkeypatch.setattr(config, "replace_transactions", lambda purchases, sales: calls.__setitem__("transactions", calls["transactions"] + 1))
    monkeypatch.setattr(config, "accounts_replace_all", lambda data: calls.__setitem__("accounts", calls["accounts"] + 1))
    monkeypatch.setattr(config, "replace_log", lambda data: calls.__setitem__("log", calls["log"] + 1))

    from app.routes.config import ImportFullBody
    body = ImportFullBody(app_config={"pipeline": {"max_discount": 9}})
    result = config.restore_full_backup_unlocked(**body.model_dump())

    assert result["ok"] is True
    assert calls["validated"] == {"pipeline": {"max_discount": 9}}
    # 未提供的 section 不应被写入；其余 section 的同一语义由
    # test_full_credentials_import_holds_buff_auth_lock 的完整事件序列覆盖。
    assert calls["credentials"] == 0


def test_full_credentials_import_holds_buff_auth_lock(monkeypatch):
    from app.services import backup as config
    from app.services import buff_auth

    events = []

    class Lock:
        def acquire(self, blocking=True):
            events.append(("lock_acquire", blocking))
            return True

        def release(self):
            events.append("lock_release")

    monkeypatch.setattr(buff_auth, "get_buff_auth_lock", lambda: Lock())
    monkeypatch.setattr(config, "save_credentials", lambda data: events.append(("save", data)))
    monkeypatch.setattr(config, "replace_transactions", lambda *args: None)
    monkeypatch.setattr(config, "accounts_replace_all", lambda *args: None)
    monkeypatch.setattr(config, "replace_log", lambda *args: None)

    from app.routes import config as config_route
    from app.routes.config import ImportFullBody
    body = ImportFullBody(credentials={"buff": {"cookies": "session=new"}})
    result = config_route.api_import_full(body)

    assert result["ok"] is True
    assert events == [
        ("lock_acquire", False),
        ("save", {"buff": {"cookies": "session=new"}}),
        "lock_release",
    ]


def test_full_import_explicit_empty_sections_clear_only_those_sections(
    monkeypatch,
):
    from app.services import backup as config

    calls = []
    monkeypatch.setattr(
        config,
        "save_credentials",
        lambda data: calls.append(("credentials", data)),
    )
    monkeypatch.setattr(
        config,
        "replace_transactions",
        lambda purchases, sales: calls.append(
            ("transactions", purchases, sales)
        ),
    )
    monkeypatch.setattr(
        config,
        "accounts_replace_all",
        lambda data: calls.append(("accounts", data)),
    )
    monkeypatch.setattr(
        config,
        "replace_log",
        lambda data: calls.append(("log", data)),
    )

    from app.routes.config import ImportFullBody
    result = config.restore_full_backup_unlocked(
        **ImportFullBody(
            credentials={},
            transactions={},
            accounts={},
            log=[],
        ).model_dump()
    )

    assert result["ok"] is True
    # 顺序无关：指定为空的四个节各自收到一次清空调用，且无其他节被覆盖
    assert sorted(calls) == sorted([
        ("credentials", {}),
        ("transactions", [], []),
        ("accounts", {}),
        ("log", []),
    ])


def test_credentials_view_only_returns_editable_fields_without_cache(monkeypatch):
    from fastapi import Response
    from app.routes import config

    monkeypatch.setattr(config, "get_steam_credentials", lambda: {
        "cookies": "sessionid=example", "steam_id": "123", "session_id": "private",
    })
    monkeypatch.setattr(config, "get_buff_credentials", lambda: {
        "cookies": "session=example", "user_agent": "Test Browser",
        "generation": 4, "source": "manual",
    })
    response = Response()
    result = config.api_get_credentials(response)
    assert response.headers["cache-control"] == "no-store"
    assert result == {
        "steam": {"cookies": "sessionid=example", "steam_id": "123"},
        "buff": {"cookies": "session=example", "user_agent": "Test Browser"},
    }
