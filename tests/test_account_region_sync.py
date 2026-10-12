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


def test_region_sync_relogins_once_when_wallet_requires_live_session(monkeypatch):
    """区域同步撞上失效会话时必须先自动重登再重试。

    旧行为直接失败并暂停自动出售安全许可——而 steamLoginSecure 是约 24 小时过期
    的 JWT，应用下次启动时几乎必然撞上失效凭据，即使其他流程随后修好也不会重试。
    """
    import steam.session as steam_session_module
    from app.services import account_region, steam_auth
    from steam import account as gift_engine

    state = {"cookies": "steamLoginSecure=dead", "relogins": 0}
    updates = {}
    monkeypatch.setattr(account_region, "get_account", lambda account_id: {"id": account_id})
    monkeypatch.setattr(
        account_region, "get_steam_credentials", lambda: {"cookies": state["cookies"]}
    )
    monkeypatch.setattr(
        account_region,
        "update_account",
        lambda account_id, **kwargs: updates.setdefault("payload", kwargs) or {"id": account_id},
    )
    monkeypatch.setattr(steam_session_module, "build_steam_session", lambda **_kw: object())
    monkeypatch.setattr(steam_session_module, "check_login_state", lambda _session: False)

    def _relogin():
        state["relogins"] += 1
        state["cookies"] = "steamLoginSecure=fresh"
        return True, "auto_ok", "已自动登录并更新凭证"

    monkeypatch.setattr(steam_auth, "try_steam_auto_relogin", _relogin)

    wallet_calls = []

    def _wallet(cookies):
        wallet_calls.append(cookies)
        if len(wallet_calls) == 1:
            raise RuntimeError("无法获取 Steam 钱包余额，请确认 Cookie 有效且账户已设置钱包")
        return {"currency_code": "CNY", "currency_id": 23, "wallet_country": "CN"}

    monkeypatch.setattr(gift_engine, "get_wallet_balance", _wallet)

    result = account_region.refresh_account_region_currency("acc1")

    assert result["ok"] is True
    assert result["currency_code"] == "CNY"
    assert state["relogins"] == 1
    assert wallet_calls == ["steamLoginSecure=dead", "steamLoginSecure=fresh"]
    assert updates["payload"]["region_check_ok"] is True


def test_region_sync_does_not_password_login_when_state_unknown(monkeypatch):
    """探测结果未知（网络抖动、风控限流）不得触发密码登录，只按失败记录。"""
    import steam.session as steam_session_module
    from app.services import account_region, steam_auth
    from steam import account as gift_engine

    updates = {}
    monkeypatch.setattr(account_region, "get_account", lambda account_id: {"id": account_id})
    monkeypatch.setattr(
        account_region, "get_steam_credentials", lambda: {"cookies": "steamLoginSecure=dead"}
    )
    monkeypatch.setattr(
        account_region,
        "update_account",
        lambda account_id, **kwargs: updates.setdefault("payload", kwargs) or {"id": account_id},
    )
    monkeypatch.setattr(steam_session_module, "build_steam_session", lambda **_kw: object())
    monkeypatch.setattr(steam_session_module, "check_login_state", lambda _session: None)
    monkeypatch.setattr(
        steam_auth,
        "try_steam_auto_relogin",
        lambda: (_ for _ in ()).throw(AssertionError("ambiguous probe must not relogin")),
    )
    monkeypatch.setattr(
        gift_engine,
        "get_wallet_balance",
        lambda _cookies: (_ for _ in ()).throw(RuntimeError("无法获取 Steam 钱包余额")),
    )

    result = account_region.refresh_account_region_currency("acc1")

    assert result["ok"] is False
    assert "钱包余额" in result["error"]
    assert updates["payload"]["region_check_ok"] is False


def test_region_sync_reuses_credentials_refreshed_by_other_flows(monkeypatch):
    """其他流程（库存扫描/保活）已重登并更新凭据时直接复用，不重复登录。

    启动时各 worker 并发，区域同步可能手持重登前的快照，而磁盘凭据已可用。"""
    import steam.session as steam_session_module
    from app.services import account_region, steam_auth
    from steam import account as gift_engine

    monkeypatch.setattr(account_region, "get_account", lambda account_id: {"id": account_id, "steam_id": ""})
    monkeypatch.setattr(
        account_region,
        "get_steam_credentials",
        lambda: {"cookies": "steamLoginSecure=fresh", "steam_id": ""},
    )
    monkeypatch.setattr(
        account_region,
        "update_account",
        lambda account_id, **_kwargs: {"id": account_id},
    )
    monkeypatch.setattr(
        steam_session_module, "build_steam_session", lambda **kwargs: kwargs.get("cookies")
    )
    monkeypatch.setattr(
        steam_session_module,
        "check_login_state",
        lambda session: session == "steamLoginSecure=fresh",
    )
    monkeypatch.setattr(
        steam_auth,
        "try_steam_auto_relogin",
        lambda: (_ for _ in ()).throw(AssertionError("fresh disk credentials must be reused")),
    )

    wallet_calls = []

    def _wallet(cookies):
        wallet_calls.append(cookies)
        if cookies == "steamLoginSecure=stale":
            raise RuntimeError("无法获取 Steam 钱包余额")
        return {"currency_code": "CNY", "currency_id": 23, "wallet_country": "CN"}

    monkeypatch.setattr(gift_engine, "get_wallet_balance", _wallet)

    result = account_region.refresh_account_region_currency(
        "acc1", cookies_raw="steamLoginSecure=stale"
    )

    assert result["ok"] is True
    assert wallet_calls == ["steamLoginSecure=stale", "steamLoginSecure=fresh"]


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


class _SessionStub:
    def __init__(self, response):
        self.response = response

    def get(self, *_args, **_kwargs):
        return self.response


def test_get_base_auth_status_strict_reads_store_user_config_country(monkeypatch):
    from steam import account as gift_engine

    config = html.escape(json.dumps({
        "webapi_token": "jwt-token",
        "account": {"country_code": "hk"},
    }), quote=True)
    page = f'<div data-store_user_config="{config}"></div>'

    monkeypatch.setattr(
        gift_engine, "build_steam_session", lambda **_kwargs: _SessionStub(_Response(page))
    )

    token, country_code, _config_data = gift_engine.get_base_auth_status(
        "steamLoginSecure=x",
        require_country=True,
    )

    assert token == "jwt-token"
    assert country_code == "HK"


def test_get_wallet_balance_returns_wallet_country(monkeypatch):
    from steam import account as gift_engine

    page = (
        'var g_rgWalletInfo = {"success":1,"wallet_balance":409,'
        '"wallet_currency":23,"wallet_country":"CN"};'
    )

    monkeypatch.setattr(
        gift_engine, "build_steam_session", lambda **_kwargs: _SessionStub(_Response(page))
    )

    wallet = gift_engine.get_wallet_balance("steamLoginSecure=x")

    assert wallet["currency_code"] == "CNY"
    assert wallet["wallet_country"] == "CN"
    assert wallet["country_code"] == "CN"


def test_get_wallet_balance_fallback_returns_currency_for_zero_balance(monkeypatch):
    from steam import account as gift_engine

    payload = {
        "response": {
            "balance": 0,
            "delayed_balance": 0,
            "currency": 23,
            "country": "CN",
        }
    }

    monkeypatch.setattr(gift_engine, "get_base_auth_status", lambda cookies: ("jwt-token", "CN", {}))
    monkeypatch.setattr(
        gift_engine, "build_steam_session", lambda **_kwargs: _SessionStub(_Response("", payload))
    )

    wallet = gift_engine.get_wallet_balance("steamLoginSecure=x")

    assert wallet["balance_raw"] == 0
    assert wallet["currency_code"] == "CNY"
    assert wallet["currency_id"] == 23


def test_get_wallet_balance_rejects_unknown_currency_id(monkeypatch):
    from steam import account as gift_engine

    page = (
        'var g_rgWalletInfo = {"success":1,"wallet_balance":409,'
        '"wallet_currency":999,"wallet_country":"CN"};'
    )

    monkeypatch.setattr(
        gift_engine, "build_steam_session", lambda **_kwargs: _SessionStub(_Response(page))
    )

    try:
        gift_engine.get_wallet_balance("steamLoginSecure=x")
    except RuntimeError as exc:
        assert "未知 Steam 钱包币种 ID: 999" in str(exc)
    else:
        raise AssertionError("unknown wallet currency must not default to USD")
