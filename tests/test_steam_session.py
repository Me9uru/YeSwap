"""Steam 会话工厂契约：浏览器指纹、cookies、代理策略与登录态探测。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from steam.session import build_steam_session, check_login_state, create_market_session


class _ProxyManager:
    def __init__(self, proxies):
        self.proxies = proxies

    def get_proxies_for_request(self, failed=False):
        return self.proxies


def test_market_session_uses_browser_impersonation_without_hardcoded_ua(monkeypatch):
    import utils.proxy_manager as proxy_manager

    monkeypatch.setattr(proxy_manager, "get_proxy_manager", lambda: _ProxyManager(None))
    session = create_market_session("sessionid=abc; steamLoginSecure=xyz", "7656119")

    # 指纹伪装是修复 Steam 风控 429 的核心；手写浏览器 UA 会与 TLS 指纹
    # 不一致，反而必然被拒。
    assert session.impersonate == "chrome"
    assert "User-Agent" not in session.headers
    assert session.verify is False
    assert session.cookies.get("sessionid") == "abc"
    assert session.headers["Referer"] == "https://steamcommunity.com/profiles/7656119/inventory/"


def test_session_proxy_follows_policy_and_explicit_empty_bypasses_it(monkeypatch):
    import utils.proxy_manager as proxy_manager

    proxies = {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"}
    monkeypatch.setattr(proxy_manager, "get_proxy_manager", lambda: _ProxyManager(proxies))

    assert build_steam_session().proxies == proxies
    # 显式传空 dict 表示明确直连，不查代理池（如下架会话此前的直连语义）。
    assert build_steam_session(proxies={}).proxies == {}


class _LoginResponse:
    def __init__(self, status_code=200, url=""):
        self.status_code = status_code
        self.url = url


class _LoginSession:
    def __init__(self, response=None, exc=None):
        self._response = response
        self._exc = exc

    def get(self, url, **kwargs):
        if self._exc is not None:
            raise self._exc
        return self._response


def test_check_login_state_is_strictly_three_valued():
    logged_in = _LoginSession(_LoginResponse(200, "https://steamcommunity.com/profiles/7656119/"))
    login_page = _LoginSession(
        _LoginResponse(200, "https://steamcommunity.com/login/home/?goto=%2Fmy%2F")
    )
    forbidden = _LoginSession(_LoginResponse(403, "https://steamcommunity.com/my/"))
    unknown = _LoginSession(_LoginResponse(200, "https://steamcommunity.com/robots.txt"))
    network_down = _LoginSession(exc=RuntimeError("network down"))

    assert check_login_state(logged_in) is True
    assert check_login_state(login_page) is False
    assert check_login_state(forbidden) is False
    # 无法判定的情况必须是 None，调用方不得据此认定登录已过期。
    assert check_login_state(unknown) is None
    assert check_login_state(network_down) is None
