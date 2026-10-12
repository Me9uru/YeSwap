import sys
import types
import threading

import pytest


@pytest.fixture(autouse=True)
def _isolated_global_buff_policy(monkeypatch):
    import buff

    policy = buff.BuffRequestPolicy(
        min_interval=0.0,
        state_path=None,
        persist=False,
    )
    monkeypatch.setattr(buff, "get_global_policy", lambda: policy)
    return policy


def test_manual_buff_cookie_requires_session(monkeypatch):
    from app.services import auth_session as auth

    saved = []
    monkeypatch.setattr(auth, "update_buff_creds", lambda cookie, **kwargs: saved.append((cookie, kwargs)))

    result = auth.api_auth_manual_cookie("buff", cookies="csrf_token=abc")

    assert result["ok"] is False
    assert "session" in result["error"]
    assert saved == []


@pytest.mark.parametrize(
    ("step", "should_resume"),
    [
        ("BUFF_AUTH_EXPIRED", True),
        ("BUFF_WRITE_RESULT_UNKNOWN", False),
        ("BUFF_ORDER_CREATED_PENDING", False),
        ("CHECKOUT_PENDING", False),
    ],
)
def test_cookie_update_only_resumes_pure_auth_failures(
    monkeypatch,
    step,
    should_resume,
):
    from app.runtime import state
    from app.services import auth_session as auth

    starts = []
    monkeypatch.setattr(
        state,
        "get_status",
        lambda: {"status": "error", "step": step},
    )
    monkeypatch.setattr(auth, "_start_purchase_pipeline", lambda config: starts.append(config) or True)
    monkeypatch.setattr(auth, "load_app_config_validated", lambda: {"test": True})
    monkeypatch.setattr(auth, "set_buff_auth_expired", lambda _value: None)
    monkeypatch.setattr(auth, "set_buff_verification_required", lambda _value: None)

    auth._maybe_resume_after_buff_cookie_update()

    assert bool(starts) is should_resume


def test_relogin_finish_surfaces_worker_error(monkeypatch):
    from app.services import auth_session as auth

    done = threading.Event()
    done.set()
    wake = threading.Event()

    monkeypatch.setattr(auth, "_relogin_context", object())
    monkeypatch.setattr(auth, "_relogin_error", "missing login cookie")
    monkeypatch.setattr(auth, "_relogin_done", done)
    monkeypatch.setattr(auth, "_relogin_wake", wake)

    result = auth._relogin_finish(True)

    assert result == {"ok": False, "error": "missing login cookie"}
    assert wake.is_set()


def test_buff_auto_relogin_success_clears_auth_and_verification(monkeypatch):
    from app.services import buff_auth

    calls = []

    class FakePage:
        url = "https://buff.163.com/"

        def goto(self, *args, **kwargs):
            return None

        def wait_for_timeout(self, *args, **kwargs):
            return None

        def title(self):
            return "BUFF"

        def content(self):
            return "<html><body>market</body></html>"

        def evaluate(self, script):
            if "navigator.userAgent" in script:
                return "Test Browser/1.0"
            return {
                "status": 200,
                "url": "https://buff.163.com/api/market/buy_order/history",
                "contentType": "application/json",
                "body": '{"code":"OK","data":{"items":[]}}',
            }

    class FakeContext:
        pages = [FakePage()]

        def cookies(self, urls=None):
            calls.append(("cookie_urls", urls))
            return [
                {"name": "session", "value": "ok"},
                {"name": "csrf_token", "value": "csrf"},
            ]

        def close(self):
            return None

    class FakeChromium:
        def launch_persistent_context(self, *args, **kwargs):
            return FakeContext()

    class FakePlaywright:
        def __enter__(self):
            return types.SimpleNamespace(chromium=FakeChromium())

        def __exit__(self, exc_type, exc, tb):
            return False

    playwright_pkg = types.ModuleType("playwright")
    sync_api = types.ModuleType("playwright.sync_api")
    sync_api.sync_playwright = lambda: FakePlaywright()
    monkeypatch.setitem(sys.modules, "playwright", playwright_pkg)
    monkeypatch.setitem(sys.modules, "playwright.sync_api", sync_api)
    monkeypatch.setattr(
        buff_auth,
        "get_buff_credentials",
        lambda: {"cookies": "session=old", "source": "playwright", "user_agent": "Test Browser/1.0"},
    )
    monkeypatch.setattr(
        buff_auth,
        "update_buff_creds",
        lambda cookie, **kwargs: calls.append(("update", cookie, kwargs)),
    )
    monkeypatch.setattr(
        buff_auth,
        "clear_buff_request_policy_after_verification",
        lambda account_id=buff_auth.BUFF_ACCOUNT_ID: calls.append(("clear_policy", account_id)) or True,
    )
    monkeypatch.setattr(buff_auth, "set_buff_auth_expired", lambda value: calls.append(("auth", value)))
    monkeypatch.setattr(
        buff_auth,
        "set_buff_verification_required",
        lambda value, reason="": calls.append(("verify", value, reason)),
    )

    result = buff_auth._try_buff_auto_relogin_impl()

    assert result[0] is True
    assert ("auth", False) in calls
    assert any(call[:2] == ("verify", False) for call in calls)
    assert ("clear_policy", "default") in calls


def test_relogin_context_retries_with_temp_profile(monkeypatch, tmp_path):
    from app.services import auth_session as auth

    calls = []

    class FakeChromium:
        def launch_persistent_context(self, profile_dir, **kwargs):
            calls.append(profile_dir)
            if len(calls) == 1:
                raise RuntimeError("BrowserType.launch_persistent_context: Target page, context or browser has been closed")
            return object()

    temp_profile = tmp_path / "temp_profile"
    monkeypatch.setattr(auth.tempfile, "mkdtemp", lambda prefix, dir: str(temp_profile))

    context, temp_dir = auth._launch_relogin_context(
        types.SimpleNamespace(chromium=FakeChromium()),
        tmp_path / "playwright_buff",
        "buff",
    )

    assert context is not None
    assert temp_dir == temp_profile
    assert len(calls) == 2


def test_browser_launch_error_is_user_friendly():
    from app.services import auth_session as auth

    raw = (
        "BrowserType.launch_persistent_context: Target page, context or browser has been closed\n"
        "Browser logs:\n<launching> very long chromium command"
    )

    message = auth._friendly_browser_launch_error(RuntimeError(raw), "buff", retried=True)

    assert "Buff" in message
    assert "完整错误见调试日志" in message
    assert "Browser logs" not in message


def test_closed_buff_login_window_reopens_same_profile_for_verification(
    monkeypatch,
    tmp_path,
):
    from app.services import auth_session as auth

    calls = []

    class ClosedContext:
        def close(self):
            calls.append("close_old")

    class Page:
        def goto(self, url, **kwargs):
            calls.append(("goto", url, kwargs))

    recovered_page = Page()

    class RecoveredContext:
        pages = [recovered_page]

    class Chromium:
        def launch_persistent_context(self, profile_dir, **kwargs):
            calls.append(("launch", profile_dir, kwargs))
            return RecoveredContext()

    monkeypatch.setattr(auth, "get_browser_channel", lambda: "chrome")
    profile_dir = tmp_path / "playwright_buff"

    context, page = auth._reopen_buff_verification_context(
        types.SimpleNamespace(chromium=Chromium()),
        ClosedContext(),
        profile_dir,
    )

    assert isinstance(context, RecoveredContext)
    assert page is recovered_page
    # 核心语义：必须复用同一个 profile_dir 重新打开，否则刚写入的登录态读不到。
    launched_profiles = [call[1] for call in calls if call[0] == "launch"]
    assert launched_profiles == [str(profile_dir)]


def test_steam_auto_relogin_does_not_reuse_cookie_from_different_account(monkeypatch):
    from app.services import steam_auth

    calls = []
    monkeypatch.setattr(
        steam_auth,
        "get_current_account",
        lambda: {"id": "acc-new", "username": "new-user", "password": "pw", "steam_id": "222"},
    )
    monkeypatch.setattr(steam_auth, "set_current", lambda account_id: calls.append(("set_current", account_id)))
    monkeypatch.setattr(
        steam_auth,
        "get_steam_credentials",
        lambda: {
            "cookies": "sessionid=old; steamLoginSecure=111%7C%7Cold-token",
            "steam_id": "111",
        },
    )
    monkeypatch.setattr(
        steam_auth,
        "_verify_steam_cookies_valid",
        lambda cookies: (_ for _ in ()).throw(AssertionError("mismatched cookie must not be reused")),
    )
    monkeypatch.setattr(
        steam_auth,
        "_do_steampy_login",
        lambda username, password, guard: (
            True,
            "",
            {"sessionid": "new-session", "steamLoginSecure": "222%7C%7Cnew-token"},
        ),
    )
    monkeypatch.setattr(
        steam_auth,
        "update_steam_creds",
        lambda cookie, session_id, steam_id=None: calls.append(("update_creds", cookie, session_id, steam_id)),
    )
    monkeypatch.setattr(steam_auth, "fetch_steam_profile_via_api", lambda steam_id, cookies: ("New User", "avatar"))
    monkeypatch.setattr(
        steam_auth,
        "update_account",
        lambda account_id, **kwargs: calls.append(("update_account", account_id, kwargs)),
    )
    monkeypatch.setattr(steam_auth, "load_app_config_validated", lambda: {})

    result = steam_auth._try_steam_auto_relogin_impl()

    assert result[0] is True
    assert any(call[0] == "update_creds" and "222%7C%7Cnew-token" in call[1] for call in calls)


def test_cookie_validity_probe_is_strict_and_proxy_aware(monkeypatch):
    """只有明确确认已登录才复用 Cookie；无法判定时不得跳过真实登录。

    旧实现用直连 requests 且网络异常时 fail-open 返回 True，受限网络下
    过期 Cookie 会被误判为有效，真实登录永远被跳过。"""
    import steam.session as steam_session_module
    from app.services import steam_auth

    monkeypatch.setattr(
        steam_session_module, "build_steam_session", lambda **_kwargs: object()
    )

    verdicts = iter([True, False, None])
    monkeypatch.setattr(
        steam_session_module, "check_login_state", lambda _session: next(verdicts)
    )

    assert steam_auth._verify_steam_cookies_valid("steamLoginSecure=token") is True
    assert steam_auth._verify_steam_cookies_valid("steamLoginSecure=token") is False
    # 无法判定（None）按"未确认有效"处理，让调用方继续真实登录。
    assert steam_auth._verify_steam_cookies_valid("steamLoginSecure=token") is False
    # 缺少 steamLoginSecure 时不发请求。
    assert steam_auth._verify_steam_cookies_valid("sessionid=only") is False


def test_relogin_bounce_prefers_community_domain_cookies(monkeypatch):
    """登录后社区握手必须确认登录态成立，并优先采用社区域名 Cookie。"""
    import steam.session as steam_session_module
    from app.services import steam_auth

    class _Cookie:
        def __init__(self, name, value, domain=""):
            self.name, self.value, self.domain = name, value, domain

    class _Session:
        cookies = type("Jar", (), {"jar": [
            _Cookie("steamLoginSecure", "store-token"),
            _Cookie("steamLoginSecure", "community-token", ".steamcommunity.com"),
            _Cookie("sessionid", "fresh-sid"),
        ]})()

    monkeypatch.setattr(steam_session_module, "build_steam_session", lambda **kw: _Session())
    monkeypatch.setattr(steam_session_module, "check_login_state", lambda _session: True)

    merged = steam_auth._ensure_community_session(
        {"steamLoginSecure": "store-token", "sessionid": "old-sid"}
    )
    assert merged["steamLoginSecure"] == "community-token"
    assert merged["sessionid"] == "fresh-sid"


def test_relogin_bounce_unconfirmed_session_signals_failure(monkeypatch):
    """握手确认不了社区域名登录态时必须返回 None，不能把未确认凭据当成功。"""
    import steam.session as steam_session_module
    from app.services import steam_auth

    def _boom(**_kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(steam_session_module, "build_steam_session", _boom)
    assert steam_auth._ensure_community_session({"steamLoginSecure": "store-token"}) is None

    # 会话能建立但 /my/ 仍显示未登录（如被风控）时同样按失败处理。
    monkeypatch.setattr(steam_session_module, "build_steam_session", lambda **kw: object())
    monkeypatch.setattr(steam_session_module, "check_login_state", lambda _session: False)
    assert steam_auth._ensure_community_session({"steamLoginSecure": "store-token"}) is None


def test_steam_cookie_probe_persists_rotated_tokens(monkeypatch):
    """验证通过时必须回写被 Steam 轮换的令牌。

    靠 steamRefresh_steam 换发的 steamLoginSecure 如果只留在内存会话里，
    存储凭据（约 24 小时 JWT）到期后照样失效，只能再跑密码登录。"""
    import steam.session as steam_session_module
    from app.services import steam_auth

    class _Cookie:
        def __init__(self, name, value, domain=""):
            self.name, self.value, self.domain = name, value, domain

    class _Session:
        def __init__(self, jar):
            self.cookies = type("Jar", (), {"jar": jar})()

    def _jar():
        return [
            _Cookie("sessionid", "sid"),
            _Cookie("steamLoginSecure", "fresh-token", ".steamcommunity.com"),
            _Cookie("steamRefresh_steam", "refresh-2", ".steampowered.com"),
        ]

    sessions = iter([_Session(_jar()), _Session(_jar())])
    monkeypatch.setattr(
        steam_session_module, "build_steam_session", lambda **_kw: next(sessions)
    )
    monkeypatch.setattr(steam_session_module, "check_login_state", lambda _session: True)
    saved = []
    monkeypatch.setattr(
        steam_auth,
        "update_steam_creds",
        lambda cookie_str, session_id="": saved.append((cookie_str, session_id)),
    )

    stale = "steamLoginSecure=old; steamRefresh_steam=refresh-1"
    assert steam_auth._verify_steam_cookies_valid(stale) is True
    assert len(saved) == 1
    assert "steamLoginSecure=fresh-token" in saved[0][0]
    assert "steamRefresh_steam=refresh-2" in saved[0][0]
    assert saved[0][1] == "sid"

    # jar 与存储一致时不重复写盘（保活探测每天跑多次，避免无谓的文件改动）。
    current = "sessionid=sid; steamLoginSecure=fresh-token; steamRefresh_steam=refresh-2"
    assert steam_auth._verify_steam_cookies_valid(current) is True
    assert len(saved) == 1


def test_steam_credential_persistence_keeps_refresh_token():
    """落盘凭据必须保留 steamRefresh_steam 刷新令牌。

    过滤掉它会让约 24 小时过期的 steamLoginSecure 无法再经 jwt/refresh 静默
    续期，每次到期都退化成一次密码登录。无关 Cookie 仍不得入库。"""
    from config import get_steam, update_steam_credentials
    from utils.cookies import parse_cookies

    update_steam_credentials(
        "sessionid=sid; steamLoginSecure=tok; steamRefresh_steam=refresh; browserid=ignored",
        "sid",
    )

    stored = parse_cookies(get_steam()["cookies"])
    assert stored["steamRefresh_steam"] == "refresh"
    assert stored["steamLoginSecure"] == "tok"
    assert "browserid" not in stored
    assert get_steam()["session_id"] == "sid"
