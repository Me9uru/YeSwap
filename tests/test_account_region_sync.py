import html
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


class _ProxyManager:
    def get_proxies_for_request(self, failed=False):
        return None


class _Response:
    status_code = 200

    def __init__(self, text="", payload=None):
        self.text = text
        self._payload = payload or {}

    def json(self):
        return self._payload


def test_refresh_account_region_currency_uses_settlement_currency_as_region_source(monkeypatch):
    from app.services import account_region
    from steam import account as gift_engine

    updates = {}
    monkeypatch.setattr(account_region, "get_account", lambda account_id: {"id": account_id})
    monkeypatch.setattr(account_region, "get_steam_credentials", lambda: {"cookies": "steamLoginSecure=x"})
    monkeypatch.setattr(
        account_region,
        "update_account",
        lambda account_id, **kwargs: updates.setdefault("payload", kwargs) or {"id": account_id, **kwargs},
    )
    monkeypatch.setattr(
        gift_engine,
        "get_wallet_balance",
        lambda cookies: {"currency_code": "CNY", "currency_id": 23, "wallet_country": "IN"},
    )

    result = account_region.refresh_account_region_currency("acc1")

    assert result["ok"] is True
    assert result["currency_code"] == "CNY"
    assert result["region_code"] == "CN"
    assert updates["payload"]["region_code"] == "CN"
    assert updates["payload"]["region_check_ok"] is True


@pytest.mark.parametrize(
    ("wallet", "cookies", "skip_unconfigured", "expected"),
    [
        # 未知结算币种 ID：回退到钱包国家
        pytest.param(
            {"currency_code": "XYZ", "currency_id": 99, "wallet_country": "CN"},
            "steamLoginSecure=x",
            False,
            {
                "ok": True,
                "currency_code": "XYZ",
                "region_code": "CN",
                "payload": {"region_check_ok": True},
            },
            id="unknown-currency-falls-back-to-wallet-country",
        ),
        # 钱包未返回结算币种：标记账号不安全
        pytest.param(
            {"currency_id": 0},
            "steamLoginSecure=x",
            False,
            {
                "ok": False,
                "error": "结算币种",
                "payload": {"region_check_ok": False},
            },
            id="missing-currency-is-unsafe",
        ),
        # 启动期缺少 Cookie：跳过同步且不写回账号
        pytest.param(
            None,
            "",
            True,
            {"ok": False, "skipped": True, "status": "missing_cookie"},
            id="missing-cookie-skips-during-startup",
        ),
        # 非启动期缺少 Cookie：标记不安全并记录原因
        pytest.param(
            None,
            "",
            False,
            {
                "ok": False,
                "error": "Cookie",
                "payload": {
                    "region_check_ok": False,
                    "region_check_error": "Cookie",
                },
            },
            id="missing-cookie-is-unsafe-when-strict",
        ),
    ],
)
def test_refresh_account_region_currency_branches(
    monkeypatch,
    wallet,
    cookies,
    skip_unconfigured,
    expected,
):
    from app.services import account_region
    from steam import account as gift_engine

    updates = {}
    monkeypatch.setattr(account_region, "get_account", lambda account_id: {"id": account_id})
    monkeypatch.setattr(account_region, "get_steam_credentials", lambda: {"cookies": cookies})
    monkeypatch.setattr(
        account_region,
        "update_account",
        lambda account_id, **kwargs: updates.setdefault("payload", kwargs) or {"id": account_id, **kwargs},
    )
    if wallet is not None:
        monkeypatch.setattr(gift_engine, "get_wallet_balance", lambda _cookies: wallet)

    result = account_region.refresh_account_region_currency(
        "acc1",
        skip_unconfigured=skip_unconfigured,
    )

    assert result["ok"] is expected["ok"]
    for key in ("currency_code", "region_code", "status"):
        if key in expected:
            assert result[key] == expected[key]
    if "skipped" in expected:
        assert result["skipped"] is expected["skipped"]
    if "error" in expected:
        assert expected["error"] in result["error"]
    if "payload" in expected:
        payload = updates["payload"]
        for key, value in expected["payload"].items():
            # 错误信息只校验要点，其余按精确值比对
            if isinstance(value, str):
                assert value in payload[key]
            else:
                assert payload[key] is value
    else:
        assert "payload" not in updates


def test_refresh_account_region_currency_rejects_cookie_for_different_account(monkeypatch):
    from app.services import account_region
    from steam import account as gift_engine

    updates = {}
    monkeypatch.setattr(account_region, "get_account", lambda account_id: {"id": account_id, "steam_id": "222"})
    monkeypatch.setattr(
        account_region,
        "get_steam_credentials",
        lambda: {
            "cookies": "sessionid=s; steamLoginSecure=111%7C%7Cold-token",
            "steam_id": "111",
        },
    )
    monkeypatch.setattr(
        account_region,
        "update_account",
        lambda account_id, **kwargs: updates.setdefault("payload", kwargs) or {"id": account_id, **kwargs},
    )
    monkeypatch.setattr(
        gift_engine,
        "get_wallet_balance",
        lambda cookies: (_ for _ in ()).throw(AssertionError("mismatched cookie must not fetch wallet")),
    )

    result = account_region.refresh_account_region_currency("acc1")

    assert result["ok"] is False
    assert "不一致" in result["error"]
    assert updates["payload"]["region_check_ok"] is False


def test_get_base_auth_status_strict_reads_store_user_config_country(monkeypatch):
    from steam import account as gift_engine
    import utils.proxy_manager as proxy_manager

    config = html.escape(json.dumps({
        "webapi_token": "jwt-token",
        "account": {"country_code": "hk"},
    }), quote=True)
    page = f'<div data-store_user_config="{config}"></div>'

    monkeypatch.setattr(proxy_manager, "get_proxy_manager", lambda: _ProxyManager())
    monkeypatch.setattr(gift_engine.requests, "get", lambda *args, **kwargs: _Response(page))

    token, country_code, _config_data = gift_engine.get_base_auth_status(
        "steamLoginSecure=x",
        require_country=True,
    )

    assert token == "jwt-token"
    assert country_code == "HK"


def test_get_wallet_balance_returns_wallet_country(monkeypatch):
    from steam import account as gift_engine
    import utils.proxy_manager as proxy_manager

    page = (
        'var g_rgWalletInfo = {"success":1,"wallet_balance":409,'
        '"wallet_currency":23,"wallet_country":"CN"};'
    )

    monkeypatch.setattr(proxy_manager, "get_proxy_manager", lambda: _ProxyManager())
    monkeypatch.setattr(gift_engine.requests, "get", lambda *args, **kwargs: _Response(page))

    wallet = gift_engine.get_wallet_balance("steamLoginSecure=x")

    assert wallet["currency_code"] == "CNY"
    assert wallet["wallet_country"] == "CN"
    assert wallet["country_code"] == "CN"


def test_get_wallet_balance_fallback_returns_currency_for_zero_balance(monkeypatch):
    from steam import account as gift_engine
    import utils.proxy_manager as proxy_manager

    payload = {
        "response": {
            "balance": 0,
            "delayed_balance": 0,
            "currency": 23,
            "country": "CN",
        }
    }

    monkeypatch.setattr(proxy_manager, "get_proxy_manager", lambda: _ProxyManager())
    monkeypatch.setattr(gift_engine, "get_base_auth_status", lambda cookies: ("jwt-token", "CN", {}))
    monkeypatch.setattr(gift_engine.requests, "get", lambda *args, **kwargs: _Response("", payload))

    wallet = gift_engine.get_wallet_balance("steamLoginSecure=x")

    assert wallet["balance_raw"] == 0
    assert wallet["currency_code"] == "CNY"
    assert wallet["currency_id"] == 23


def test_get_wallet_balance_rejects_unknown_currency_id(monkeypatch):
    from steam import account as gift_engine
    import utils.proxy_manager as proxy_manager

    page = (
        'var g_rgWalletInfo = {"success":1,"wallet_balance":409,'
        '"wallet_currency":999,"wallet_country":"CN"};'
    )

    monkeypatch.setattr(proxy_manager, "get_proxy_manager", lambda: _ProxyManager())
    monkeypatch.setattr(gift_engine.requests, "get", lambda *args, **kwargs: _Response(page))

    try:
        gift_engine.get_wallet_balance("steamLoginSecure=x")
    except RuntimeError as exc:
        assert "未知 Steam 钱包币种 ID: 999" in str(exc)
    else:
        raise AssertionError("unknown wallet currency must not default to USD")


def test_gift_balance_rejects_cookie_for_different_current_account(monkeypatch):
    from app.routes import gift

    monkeypatch.setattr(
        gift,
        "get_steam_credentials",
        lambda: {
            "cookies": "sessionid=old; steamLoginSecure=111%7C%7Cold-token",
            "steam_id": "111",
        },
    )
    monkeypatch.setattr(gift, "get_current_account", lambda: {"id": "acc-new", "steam_id": "222"})

    result = gift.api_gift_balance()

    assert result["ok"] is False
    assert "不一致" in result["error"]


def test_gift_balance_syncs_wallet_currency_to_current_account(monkeypatch):
    from app.routes import gift

    updates = {}
    monkeypatch.setattr(
        gift,
        "get_steam_credentials",
        lambda: {
            "cookies": "sessionid=s; steamLoginSecure=222%7C%7Ctoken",
            "steam_id": "222",
        },
    )
    monkeypatch.setattr(gift, "get_current_account", lambda: {"id": "acc-new", "steam_id": "222"})
    monkeypatch.setattr(
        gift.gift_engine,
        "get_wallet_balance",
        lambda cookies: {
            "balance_raw": 409,
            "balance_display": "¥4.09",
            "currency_code": "CNY",
            "currency_symbol": "¥",
            "currency_id": 23,
            "wallet_country": "CN",
            "country_code": "CN",
        },
    )
    monkeypatch.setattr(
        gift,
        "update_account",
        lambda account_id, **kwargs: updates.setdefault("payload", {"account_id": account_id, **kwargs}),
    )

    result = gift.api_gift_balance()

    assert result["ok"] is True
    assert result["currency_code"] == "CNY"
    assert updates["payload"]["account_id"] == "acc-new"
    assert updates["payload"]["currency_code"] == "CNY"
    assert updates["payload"]["region_code"] == "CN"
