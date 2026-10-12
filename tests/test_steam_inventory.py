"""库存链路契约：429 限流信号、冷却期跳过请求、登录态消歧。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from steam.inventory import SteamRateLimited


class _Response:
    def __init__(self, status_code=200, payload=None, headers=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.headers = headers or {}

    def json(self):
        return self._payload


class _Session:
    def __init__(self, responses):
        self._responses = list(responses)

    def get(self, url, **kwargs):
        if not self._responses:
            raise AssertionError("unexpected extra inventory request")
        return self._responses.pop(0)


def test_inventory_429_raises_rate_limited_with_retry_after():
    from steam.inventory import fetch_cs2_inventory

    session = _Session([_Response(429, headers={"Retry-After": "42"})])
    with pytest.raises(SteamRateLimited) as exc_info:
        fetch_cs2_inventory(session, "7656119")
    assert exc_info.value.retry_after == pytest.approx(42.0)


def test_inventory_429_without_header_defers_to_business_backoff():
    from steam.inventory import fetch_cs2_inventory

    session = _Session([_Response(429)])
    with pytest.raises(SteamRateLimited) as exc_info:
        fetch_cs2_inventory(session, "7656119")
    assert exc_info.value.retry_after == 0.0


def test_inventory_403_surfaces_auth_expired_marker():
    from steam.inventory import fetch_cs2_inventory

    session = _Session([_Response(403)])
    assert fetch_cs2_inventory(session, "7656119") == {"auth_expired": True}


def _prepare_scan(monkeypatch, *, now=100.0):
    from app.steam_market import inventory as app_inventory

    monkeypatch.setattr(app_inventory.time, "monotonic", lambda: now)
    monkeypatch.setattr(app_inventory, "_inventory_rate_limit_until", 0.0)
    monkeypatch.setattr(app_inventory, "_inventory_rate_limit_streak", 0)
    monkeypatch.setattr(
        app_inventory,
        "get_steam",
        lambda: {"steam_id": "1", "cookies": "steamLoginSecure=x"},
    )
    monkeypatch.setattr(app_inventory, "create_market_session", lambda *a, **k: object())
    return app_inventory


def test_scan_records_rate_limit_cooldown_and_skips_requests(monkeypatch):
    app_inventory = _prepare_scan(monkeypatch)
    calls = []

    def limited(*_args, **_kwargs):
        calls.append(True)
        raise SteamRateLimited(5.0)

    monkeypatch.setattr(app_inventory, "fetch_cs2_inventory", limited)

    ok, items, err = app_inventory.scan_cs2_inventory()
    assert ok is False and items == []
    assert "限流" in err
    # 限流不得触发密码重登（重登只在"登录已过期"时发生）。
    assert "登录已过期" not in err

    ok2, _items2, err2 = app_inventory.scan_cs2_inventory()
    assert ok2 is False
    assert "限流" in err2
    assert len(calls) == 1  # 冷却期内不再发请求


def test_scan_disambiguates_empty_inventory_by_login_state(monkeypatch):
    app_inventory = _prepare_scan(monkeypatch)
    monkeypatch.setattr(
        app_inventory,
        "fetch_cs2_inventory",
        lambda *a, **k: {"assets": [], "descriptions": []},
    )

    monkeypatch.setattr(app_inventory, "check_login_state", lambda _s: False)
    ok, items, err = app_inventory.scan_cs2_inventory()
    assert ok is False and items == []
    assert "登录已过期" in err

    monkeypatch.setattr(app_inventory, "check_login_state", lambda _s: True)
    ok, items, err = app_inventory.scan_cs2_inventory()
    assert ok is True and items == [] and err == ""


def test_scan_auth_expired_signal_is_discarded_only_when_confirmed_logged_in(monkeypatch):
    app_inventory = _prepare_scan(monkeypatch)
    monkeypatch.setattr(
        app_inventory, "fetch_cs2_inventory", lambda *a, **k: {"auth_expired": True}
    )

    # ctx16 匿名形态与"Cookie 失效"形态相同：确认已登录时不得误报过期。
    monkeypatch.setattr(app_inventory, "check_login_state", lambda _s: True)
    ok, items, err = app_inventory.scan_cs2_inventory()
    assert ok is True and items == [] and err == ""

    # 探测失败（网络异常）保持原行为，报过期。
    monkeypatch.setattr(app_inventory, "check_login_state", lambda _s: None)
    ok, items, err = app_inventory.scan_cs2_inventory()
    assert ok is False and "登录已过期" in err
