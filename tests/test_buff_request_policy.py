import json
import threading

import pytest
import requests
from requests.cookies import RequestsCookieJar, create_cookie

from buff.buyer import (
    API_BILL_ORDER_BATCH_INFO,
    API_BUY,
    API_HISTORY,
    API_MANUAL_PLUS_BUYER_SEND_OFFER,
    API_PAGE_PAY,
    API_STEAM_TRADE,
    API_WX_PAY_QRCODE,
    BUY_ORDER_PAY_FAILED,
    BUY_ORDER_PAY_PAID,
    BUY_ORDER_PAY_PENDING,
    BUY_ORDER_PAY_UNKNOWN,
    PAY_METHOD_ALIPAY,
    PAY_METHOD_WECHAT,
    BuffBuyer,
)
from buff.request_policy import (
    BuffAuthExpired,
    BuffRateLimited,
    BuffRequestPolicy,
    BuffRiskControlTriggered,
    BuffVerificationRequired,
    BuffWriteResultUnknown,
    DEFAULT_MIN_REQUEST_INTERVAL,
    DEFAULT_RATE_LIMIT_SECONDS,
    account_fingerprint,
)


class FakeResponse:
    def __init__(
        self,
        data,
        *,
        status_code=200,
        headers=None,
        cookies=None,
        raw_text=None,
        url="",
        history=None,
    ):
        self._data = data
        self.status_code = status_code
        self.headers = headers or {}
        self.text = (
            raw_text
            if raw_text is not None
            else (json.dumps(data, ensure_ascii=False) if data is not None else "")
        )
        self.url = url
        self.history = history or []
        self.cookies = RequestsCookieJar()
        for name, value in (cookies or {}).items():
            self.cookies.set(name, value)

    def json(self):
        if self._data is None:
            raise ValueError("not json")
        return self._data


class FakeSession:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.close_calls = 0
        self.cookies = RequestsCookieJar()
        self.headers = {}

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        if not self.responses:
            raise AssertionError("unexpected HTTP request")
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    def close(self):
        self.close_calls += 1


def no_wait_policy(**kwargs):
    return BuffRequestPolicy(min_interval=0, state_path=None, persist=False, **kwargs)


def test_default_user_agent_is_stable_and_can_be_overridden():
    custom = BuffBuyer(
        "session=three", user_agent="Browser/7", request_policy=no_wait_policy()
    )

    assert custom.headers["User-Agent"] == "Browser/7"
    assert custom.user_agent == "Browser/7"


def test_generic_alipay_uses_wallet_channel_not_credit_card_huabei():
    # 对 BUFF 购买预检的实测结论：49 是通用支付宝渠道，51 是「支付宝-信用卡
    # 花呗」独立渠道。默认值退回 51 会让免密/余额扣款被路由到花呗渠道并失败。
    assert PAY_METHOD_ALIPAY == 49


def test_close_only_releases_an_internally_owned_session(monkeypatch):
    owned = FakeSession()
    monkeypatch.setattr("buff.buyer.requests.Session", lambda: owned)
    buyer = BuffBuyer("session=owned", request_policy=no_wait_policy())
    buyer.close()
    buyer.close()
    assert owned.close_calls == 1

    external = FakeSession()
    injected = BuffBuyer(
        "session=external", session=external, request_policy=no_wait_policy()
    )
    injected.close()
    assert external.close_calls == 0


def test_persistent_session_accepts_cookie_and_syncs_csrf_before_write():
    session = FakeSession(
        FakeResponse({"code": "OK"}, cookies={"csrf_token": "rotated"}),
        FakeResponse({"code": "OK"}),
    )
    buyer = BuffBuyer(
        "session=s; csrf_token=initial",
        session=session,
        request_policy=no_wait_policy(),
    )

    buyer._make_request("GET", "https://buff.163.com/api/first")
    buyer._make_request("POST", "https://buff.163.com/api/second", data="{}")

    assert len(session.calls) == 2
    assert session.calls[1][2]["headers"]["X-Csrftoken"] == "rotated"
    assert buyer.cookies_dict["csrf_token"] == "rotated"
    exported = buyer.export_cookie_string()
    assert "session=s" in exported
    assert "csrf_token=rotated" in exported
    assert "csrf_token=initial" not in exported


def test_429_retry_after_opens_and_persists_account_circuit(tmp_path):
    state_path = tmp_path / "buff-policy.json"
    session = FakeSession(
        FakeResponse(
            {"code": "TOO_MANY_REQUESTS", "msg": "slow down"},
            status_code=429,
            headers={"Retry-After": "120"},
        )
    )
    policy = BuffRequestPolicy(min_interval=0, state_path=state_path)
    buyer = BuffBuyer("session=rate-secret", session=session, request_policy=policy)

    with pytest.raises(BuffRateLimited) as first:
        buyer._make_request("GET", "https://buff.163.com/api/one")
    assert first.value.retry_after == pytest.approx(120, abs=1)

    with pytest.raises(BuffRateLimited):
        buyer._make_request("GET", "https://buff.163.com/api/two")
    assert len(session.calls) == 1

    reloaded = BuffRequestPolicy(min_interval=0, state_path=state_path)
    another_session = FakeSession(FakeResponse({"code": "OK"}))
    another = BuffBuyer(
        "session=rate-secret", session=another_session, request_policy=reloaded
    )
    with pytest.raises(BuffRateLimited):
        another._make_request("GET", "https://buff.163.com/api/three")
    assert another_session.calls == []
    assert "rate-secret" not in state_path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "raw",
    [
        # One representative per damaged-input class.  All rows below fail
        # closed, but they reach that outcome through different guards, so each
        # guard keeps a row:
        #   * truncated JSON           -> json.loads() error path (same handler
        #                                 as the duplicate-key hook)
        #   * NaN literal              -> `parse_constant` rejection, which
        #                                 also raises for Infinity
        #   * wrong version            -> top-level envelope guard (a payload
        #                                 without a version fails the same `if`)
        #   * unknown reason           -> `_CircuitState.from_json` reason guard
        #   * updated_at true / "1"    -> `updated_at` type guard
        #   * status_code "429"        -> `status_code` type guard (a `1e999`
        #                                 literal overflows to inf and is
        #                                 rejected by this same guard)
        #   * duplicate key            -> object_pairs_hook rejection
        "{broken",
        (
            '{"version": 1, "accounts": {"default": '
            '{"reason": "verification", "updated_at": NaN}}}'
        ),
        '{"version": 2, "accounts": {}}',
        '{"version": 1, "accounts": {"default": {}}}',
        (
            '{"version": 1, "accounts": {"default": '
            '{"reason": "rate_limited", "updated_at": true, '
            '"blocked_until": true, "status_code": 429}}}'
        ),
        (
            '{"version": 1, "accounts": {"default": '
            '{"reason": "verification", "updated_at": "1"}}}'
        ),
        (
            '{"version": 1, "accounts": {"default": '
            '{"reason": "rate_limited", "updated_at": 1, '
            '"blocked_until": 9999999999, "status_code": "429"}}}'
        ),
        '{"version": 1, "version": 1, "accounts": {}}',
    ],
)
def test_damaged_policy_state_fails_closed_until_explicit_clear(tmp_path, raw):
    state_path = tmp_path / "buff-policy.json"
    state_path.write_text(raw, encoding="utf-8")
    policy = BuffRequestPolicy(min_interval=0, state_path=state_path)

    with pytest.raises(BuffVerificationRequired, match="状态文件"):
        with policy.request_slot("default"):
            raise AssertionError("damaged policy must not yield a request slot")

    policy.clear("default")
    with policy.request_slot("default"):
        pass


def test_rate_limit_record_without_deadline_is_invalid_and_blocks(tmp_path):
    state_path = tmp_path / "buff-policy.json"
    state_path.write_text(
        json.dumps(
            {
                "version": 1,
                "accounts": {
                    "default": {
                        "reason": "rate_limited",
                        "updated_at": 1.0,
                        "blocked_until": None,
                        "message": "missing deadline",
                        "status_code": 429,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    policy = BuffRequestPolicy(min_interval=0, state_path=state_path)

    with pytest.raises(BuffVerificationRequired, match="状态文件"):
        policy.raise_if_blocked("default")


def test_failed_policy_replace_leaves_restart_fail_closed_marker(
    monkeypatch,
    tmp_path,
):
    import buff.request_policy as policy_module

    state_path = tmp_path / "buff-policy.json"
    policy = BuffRequestPolicy(min_interval=0, state_path=state_path)
    session = FakeSession(FakeResponse({"code": "FAIL"}, status_code=403))
    buyer = BuffBuyer(
        "session=s; csrf_token=c",
        session=session,
        request_policy=policy,
    )
    real_replace = policy_module.os.replace
    monkeypatch.setattr(
        policy_module.os,
        "replace",
        lambda *_args: (_ for _ in ()).throw(OSError("disk replace failed")),
    )

    # Persistence failure must never replace the stronger post-send write
    # classification with a pre-send verification exception.
    with pytest.raises(BuffWriteResultUnknown):
        buyer._make_request(
            "POST",
            "https://buff.163.com/api/write",
            data="{}",
        )
    with pytest.raises(BuffVerificationRequired):
        policy.raise_if_blocked(buyer.account_key)

    monkeypatch.setattr(policy_module.os, "replace", real_replace)
    reloaded = BuffRequestPolicy(min_interval=0, state_path=state_path)
    with pytest.raises(BuffVerificationRequired, match="状态文件"):
        reloaded.raise_if_blocked(buyer.account_key)

    reloaded.clear(buyer.account_key)
    reloaded.raise_if_blocked(buyer.account_key)


def test_risk_http_status_opens_persistent_circuit_until_explicit_reset(tmp_path):
    # 403 与 412 走同一个风控守卫（buyer 中 `status_code in {403, 412}`），
    # 一行即可覆盖整条分支。
    status_code = 403
    session = FakeSession(
        FakeResponse({"code": "FAIL"}, status_code=status_code),
        FakeResponse({"code": "OK"}),
    )
    state_path = tmp_path / f"risk-{status_code}.json"
    policy = BuffRequestPolicy(min_interval=0, state_path=state_path)
    buyer = BuffBuyer("session=risk", session=session, request_policy=policy)

    with pytest.raises(BuffRiskControlTriggered) as first:
        buyer._make_request("GET", "https://buff.163.com/api/one")
    assert first.value.status_code == status_code

    with pytest.raises(BuffRiskControlTriggered):
        buyer._make_request("GET", "https://buff.163.com/api/two")
    assert len(session.calls) == 1

    reloaded_session = FakeSession(FakeResponse({"code": "OK"}))
    reloaded = BuffBuyer(
        "session=risk",
        session=reloaded_session,
        request_policy=BuffRequestPolicy(min_interval=0, state_path=state_path),
    )
    with pytest.raises(BuffRiskControlTriggered):
        reloaded._make_request("GET", "https://buff.163.com/api/reloaded")
    assert reloaded_session.calls == []

    policy.reset(buyer.account_key)
    assert buyer._make_request("GET", "https://buff.163.com/api/three") == {
        "code": "OK"
    }
    assert len(session.calls) == 2


def test_get_sell_orders_non_ok_does_not_retry():
    session = FakeSession(FakeResponse({"code": "FAIL", "msg": "bad argument"}))
    buyer = BuffBuyer(
        "session=sell-orders", session=session, request_policy=no_wait_policy()
    )

    assert buyer.get_sell_orders(123) is None
    assert len(session.calls) == 1


def test_configured_request_timeout_is_used_by_default():
    session = FakeSession(FakeResponse({"code": "OK"}))
    buyer = BuffBuyer(
        "session=s",
        session=session,
        request_policy=no_wait_policy(),
        request_timeout=17,
    )

    buyer._make_request("GET", "https://buff.163.com/api/read")

    assert session.calls[0][2]["timeout"] == 17


def test_policy_enforces_hard_minimum_interval_for_same_account():
    now = [10.0]
    sleeps = []

    def clock():
        return now[0]

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    policy = no_wait_policy(clock=clock, sleeper=sleep)
    policy.min_interval = 1.25

    with policy.request_slot("account"):
        pass
    with policy.request_slot("account"):
        pass

    assert sleeps == [pytest.approx(1.25)]


def test_browser_verification_shares_pacing_but_cannot_bypass_rate_limit():
    now = [10.0]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    policy = no_wait_policy(clock=lambda: now[0], sleeper=sleep)
    policy.min_interval = 2.0
    with policy.request_slot("account"):
        pass
    policy.trip_verification("account", "verify")

    with pytest.raises(BuffVerificationRequired):
        with policy.request_slot("account"):
            pass
    with policy.verification_slot("account"):
        pass
    assert sleeps == [pytest.approx(2.0)]

    policy.trip_rate_limit("account", 60, "cooldown")
    with pytest.raises(BuffRateLimited):
        with policy.verification_slot("account"):
            raise AssertionError("429 must prevent the browser probe")
    assert sleeps == [pytest.approx(2.0)]


def test_production_defaults_use_two_second_interval_and_five_minute_429():
    assert DEFAULT_MIN_REQUEST_INTERVAL == 2.0
    assert DEFAULT_RATE_LIMIT_SECONDS == 300.0

    session = FakeSession(
        FakeResponse({"code": "TOO_MANY_REQUESTS"}, status_code=429)
    )
    buyer = BuffBuyer(
        "session=s; csrf_token=c", session=session, request_policy=no_wait_policy()
    )
    with pytest.raises(BuffRateLimited) as exc_info:
        buyer._make_request("GET", "https://buff.163.com/api/rate")
    assert exc_info.value.retry_after == pytest.approx(300.0)


def test_cooling_down_response_opens_default_rate_limit_circuit():
    session = FakeSession(
        FakeResponse({"code": "Cooling Down", "msg": "operation too fast"})
    )
    buyer = BuffBuyer(
        "session=s; csrf_token=c", session=session, request_policy=no_wait_policy()
    )

    with pytest.raises(BuffWriteResultUnknown):
        buyer._make_request("POST", API_BUY, data="{}")

    with pytest.raises(BuffRateLimited) as exc_info:
        buyer._make_request("GET", API_HISTORY)
    assert exc_info.value.retry_after == pytest.approx(DEFAULT_RATE_LIMIT_SECONDS)
    assert len(session.calls) == 1


def test_owned_session_ignores_environment_proxy_settings():
    buyer = BuffBuyer(
        "session=s; csrf_token=c", request_policy=no_wait_policy()
    )
    try:
        assert buyer.session.trust_env is False
    finally:
        buyer.close()


def test_buff_client_uses_stable_default_account_key_across_session_rotation():
    from app.services.buff_client import BuffClient

    credentials = {
        "cookies": "session=rotated; csrf_token=next",
        "generation": 2,
    }
    client = BuffClient(
        "session=initial; csrf_token=old",
        credential_generation=1,
        credentials_provider=lambda: credentials,
    )
    try:
        original_key = client._buyer.account_key
        rebuilt_key = client._run(lambda buyer: buyer.account_key)
        assert original_key == rebuilt_key == account_fingerprint({}, "default")
    finally:
        client.close()


def test_buff_client_reloads_changed_credentials_without_generation_bump():
    from app.services.buff_client import BuffClient

    credentials = {
        "cookies": "session=initial; csrf_token=old",
        "user_agent": "Browser/1",
        "generation": 1,
    }
    client = BuffClient(
        credentials["cookies"],
        user_agent=credentials["user_agent"],
        credential_generation=1,
        credentials_provider=lambda: credentials,
    )
    try:
        credentials["cookies"] = "session=rotated; csrf_token=new"
        credentials["user_agent"] = "Browser/2"

        current = client._run(
            lambda buyer: (buyer.cookies_dict["session"], buyer.user_agent)
        )

        assert current == ("rotated", "Browser/2")
    finally:
        client.close()


def test_missing_csrf_blocks_write_before_http_call():
    session = FakeSession(FakeResponse({"code": "OK"}))
    buyer = BuffBuyer(
        "session=s", session=session, request_policy=no_wait_policy()
    )

    with pytest.raises(BuffAuthExpired, match="csrf_token"):
        buyer._make_request("POST", "https://buff.163.com/api/write", data="{}")
    assert session.calls == []


def test_waiting_writer_reads_csrf_only_after_preceding_response_rotates_it():
    first_entered = threading.Event()
    release_first = threading.Event()

    class ConcurrentSession(FakeSession):
        def request(self, method, url, **kwargs):
            self.calls.append((method, url, kwargs))
            if len(self.calls) == 1:
                first_entered.set()
                assert release_first.wait(timeout=2)
                return FakeResponse(
                    {"code": "OK"}, cookies={"csrf_token": "rotated"}
                )
            return FakeResponse({"code": "OK"})

    session = ConcurrentSession()
    buyer = BuffBuyer(
        "session=s; csrf_token=initial",
        session=session,
        request_policy=no_wait_policy(),
        account_id="default",
    )
    failures = []

    def run(method, url):
        try:
            buyer._make_request(method, url, data="{}" if method == "POST" else None)
        except Exception as exc:  # pragma: no cover - assertion reports details
            failures.append(exc)

    first = threading.Thread(target=run, args=("GET", "https://buff.163.com/api/one"))
    second = threading.Thread(target=run, args=("POST", "https://buff.163.com/api/two"))
    first.start()
    assert first_entered.wait(timeout=2)
    second.start()
    release_first.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert failures == []
    assert not first.is_alive() and not second.is_alive()
    assert session.calls[1][2]["headers"]["X-Csrftoken"] == "rotated"


def test_write_network_timeout_is_explicit_unknown_result():
    session = FakeSession(requests.Timeout("timed out"))
    buyer = BuffBuyer(
        "session=s; csrf_token=c", session=session, request_policy=no_wait_policy()
    )

    with pytest.raises(BuffWriteResultUnknown) as exc_info:
        buyer._make_request("POST", "https://buff.163.com/api/write", data="{}")
    assert exc_info.value.method == "POST"
    assert exc_info.value.url.endswith("/api/write")
    assert exc_info.value.status_code is None
    assert isinstance(exc_info.value.__cause__, requests.Timeout)


def test_public_lock_method_does_not_swallow_unknown_write_result():
    session = FakeSession(requests.ConnectionError("connection dropped"))
    buyer = BuffBuyer(
        "session=s; csrf_token=c", session=session, request_policy=no_wait_policy()
    )

    with pytest.raises(BuffWriteResultUnknown):
        buyer.lock_and_get_pay_url("csgo", 1, "sell-order", "10.00")
    assert len(session.calls) == 1


@pytest.mark.parametrize(
    ("method_name", "args", "pay_method"),
    [
        ("lock_and_get_pay_url", ("csgo", 1, "sell-order", "10.00"), None),
        ("batch_buy_create", (1, 10.0, 2, "csgo"), PAY_METHOD_WECHAT),
        (
            "batch_buy_finalize",
            ("csgo", 1, "sell-order", "10.00", "batch"),
            PAY_METHOD_WECHAT,
        ),
    ],
)
@pytest.mark.parametrize(
    "success_data",
    # One representative per malformed-payload class:
    #   * ``None`` covers every non-dict payload -- ``[]`` and ``"bad-data"``
    #     are rejected by the same ``isinstance(data, dict)`` guard
    #   * ``{}`` and ``{"id": {"bad": "type"}}`` are both rejected by the value
    #     type guard inside ``_normalize_server_identifier`` (one row per
    #     missing-id / wrong-type input shape)
    #   * ``{"id": ""}`` is the only row exercising the separate
    #     empty-identifier guard
    [None, {}, {"id": ""}, {"id": {"bad": "type"}}],
)
def test_every_order_creating_write_rejects_malformed_success_data(
    method_name, args, pay_method, success_data
):
    session = FakeSession(FakeResponse({"code": "OK", "data": success_data}))
    kwargs = {
        "session": session,
        "request_policy": no_wait_policy(),
    }
    if pay_method is not None:
        kwargs["pay_method"] = pay_method
    buyer = BuffBuyer("session=s; csrf_token=c", **kwargs)

    with pytest.raises(BuffWriteResultUnknown):
        getattr(buyer, method_name)(*args)
    assert len(session.calls) == 1


@pytest.mark.parametrize(
    "response",
    [
        FakeResponse({"code": "ERROR"}, status_code=500),
        FakeResponse(
            None,
            status_code=200,
            headers={"Content-Type": "text/plain"},
            raw_text="upstream returned no JSON",
        ),
    ],
)
def test_non_idempotent_5xx_or_non_json_is_unknown_result(response):
    session = FakeSession(response)
    buyer = BuffBuyer(
        "session=s; csrf_token=c", session=session, request_policy=no_wait_policy()
    )

    with pytest.raises(BuffWriteResultUnknown) as exc_info:
        buyer._make_request("POST", "https://buff.163.com/api/write", data="{}")
    assert exc_info.value.status_code == response.status_code


@pytest.mark.parametrize(
    ("response", "circuit_exception"),
    [
        # One representative per classified write failure.  Rows that only differ
        # in HTTP status code or message wording execute the same production
        # guard, so the second spelling is dropped:
        #   403 / 412                     -> risk-control guard
        #   429 / body "RATE_LIMIT"       -> rate-limit guard
        #   "/verify" / "/maintenance"    -> navigation guard
        #   401 / body "LOGIN_REQUIRED"   -> authentication guard
        #   gateway HTML / login HTML     -> unknown-HTML guard (the login wording
        #                                    does not match the login markers)
        # "页面已过期" and "RISK"/"captcha" both stay: they are the only rows
        # covering those `_is_verification_required` markers.
        (
            FakeResponse({"code": "FAIL"}, status_code=403),
            BuffRiskControlTriggered,
        ),
        (
            FakeResponse(
                {"code": "TOO_MANY_REQUESTS"},
                status_code=429,
                headers={"Retry-After": "60"},
            ),
            BuffRateLimited,
        ),
        (
            FakeResponse(
                {"code": "OK"},
                url="https://buff.163.com/account/login",
                history=[
                    FakeResponse(
                        None,
                        status_code=302,
                        headers={"Location": "/account/login"},
                        url="https://buff.163.com/api/write",
                    )
                ],
            ),
            None,
        ),
        (
            FakeResponse(
                {"code": "OK"},
                url="https://buff.163.com/verify",
                history=[
                    FakeResponse(
                        None,
                        status_code=302,
                        headers={"Location": "/verify"},
                        url="https://buff.163.com/api/write",
                    )
                ],
            ),
            BuffVerificationRequired,
        ),
        (
            FakeResponse({"code": "LOGIN_REQUIRED"}, status_code=401),
            None,
        ),
        (
            FakeResponse({"code": "RISK", "msg": "captcha required"}),
            BuffVerificationRequired,
        ),
        (
            FakeResponse({"code": "FAIL", "msg": "页面已过期"}),
            BuffVerificationRequired,
        ),
        (
            FakeResponse(
                None,
                headers={"Content-Type": "text/html"},
                raw_text="<html><body>unexpected gateway page</body></html>",
            ),
            BuffVerificationRequired,
        ),
    ],
)
def test_every_issued_abnormal_write_is_unknown_and_keeps_relevant_circuit(
    response, circuit_exception
):
    session = FakeSession(response)
    buyer = BuffBuyer(
        "session=s; csrf_token=c",
        session=session,
        request_policy=no_wait_policy(),
        account_id="default",
    )

    with pytest.raises(BuffWriteResultUnknown):
        buyer._make_request("POST", "https://buff.163.com/api/write", data="{}")

    if circuit_exception is not None:
        with pytest.raises(circuit_exception):
            buyer._make_request("GET", "https://buff.163.com/api/read")
    assert len(session.calls) == 1


@pytest.mark.parametrize(
    ("pay_response", "expected_lookup_calls"),
    [
        # One representative per failure reason:
        #   * a transport error is swallowed by the lookup helper, so the
        #     bounded read retries are exhausted and no URL is ever returned
        #   * a 200 OK whose payload yields a non-string URL is rejected by the
        #     retry loop's string check (an OK body that is not a dict ends in
        #     the same retry-exhausted path, so it adds no branch)
        #   * an auth/circuit failure on the read side stops retrying at once
        #     (401 and 403 both raise a `BuffRequestBlocked` subclass, so the
        #     risk spelling adds no branch)
        #   * 免密（自动扣款）订单的实测签名：code=OK、data 只有 auto_pay /
        #     buff_pay_channel 渠道配置；不会产出支付链接，只查询一次即
        #     短路转入免密付款状态确认
        (requests.Timeout("payment lookup timed out"), 4),
        (FakeResponse({"code": "OK", "data": {"url": {"bad": "type"}}}), 4),
        (FakeResponse({"code": "LOGIN_REQUIRED"}, status_code=401), 1),
        (
            FakeResponse(
                {
                    "code": "OK",
                    "data": {
                        "auto_pay": {"channel": "epay"},
                        "buff_pay_channel": "alipay",
                    },
                }
            ),
            1,
        ),
    ],
)
def test_order_created_payment_lookup_failure_returns_created_pending(
    pay_response, expected_lookup_calls, monkeypatch
):
    monkeypatch.setattr("buff.buyer.jittered_sleep", lambda *_args, **_kwargs: None)
    session = FakeSession(
        FakeResponse({"code": "OK", "data": {"id": "order-created"}}),
        *[pay_response for _ in range(4)],
    )
    buyer = BuffBuyer(
        "session=s; csrf_token=c",
        session=session,
        request_policy=no_wait_policy(),
        account_id="default",
    )

    result = buyer.lock_and_get_pay_url("csgo", 1, "sell-order", "10.00")

    assert result["success"] is False
    assert result["code"] == "CREATED_WITHOUT_PAY_URL"
    assert result["created"] is True
    assert result["pay_url"] is None
    assert result["pay_type"] == "alipay"
    assert result["order_id"] == "order-created"
    assert [(method, url) for method, url, _ in session.calls] == [
        ("POST", API_BUY),
        *[("GET", API_PAGE_PAY) for _ in range(expected_lookup_calls)],
    ]


def test_payment_lookup_retries_until_qrcode_is_available(monkeypatch):
    monkeypatch.setattr("buff.buyer.jittered_sleep", lambda *_args, **_kwargs: None)
    session = FakeSession(
        FakeResponse({"code": "OK", "data": {"id": "wechat-order"}}),
        FakeResponse({"code": "OK", "data": {}}),
        FakeResponse({"code": "OK", "data": {"url": "https://pay.example/ok"}}),
    )
    buyer = BuffBuyer(
        "session=s; csrf_token=c",
        pay_method=PAY_METHOD_WECHAT,
        session=session,
        request_policy=no_wait_policy(),
    )

    result = buyer.lock_and_get_pay_url("csgo", 1, "sell-order", "10.00")

    assert result["success"] is True
    assert result["pay_url"] == "https://pay.example/ok"
    assert [(method, url) for method, url, _ in session.calls] == [
        ("POST", API_BUY),
        ("GET", API_WX_PAY_QRCODE),
        ("GET", API_WX_PAY_QRCODE),
    ]


def test_payment_lookup_block_is_preserved_after_created_pending_result():
    session = FakeSession(
        FakeResponse({"code": "OK", "data": {"id": "order-created"}}),
        FakeResponse({"code": "FAIL"}, status_code=412),
    )
    buyer = BuffBuyer(
        "session=s; csrf_token=c",
        session=session,
        request_policy=no_wait_policy(),
        account_id="default",
    )

    result = buyer.lock_and_get_pay_url("csgo", 1, "sell-order", "10.00")

    assert result["code"] == "CREATED_WITHOUT_PAY_URL"
    assert result["created"] is True
    assert result["order_id"] == "order-created"
    with pytest.raises(BuffRiskControlTriggered):
        buyer._make_request("GET", API_HISTORY)
    assert len(session.calls) == 2


def test_created_pending_result_exposes_payment_lookup_verification_reason(
    monkeypatch,
):
    monkeypatch.setattr("buff.buyer.jittered_sleep", lambda *_args, **_kwargs: None)
    session = FakeSession(
        FakeResponse({"code": "OK", "data": {"id": "order-created"}}),
        FakeResponse({"code": "FAIL", "error": "请刷新当前页面后重试"}),
    )
    buyer = BuffBuyer(
        "session=s; csrf_token=c",
        session=session,
        request_policy=no_wait_policy(),
        account_id="default",
    )

    result = buyer.lock_and_get_pay_url("csgo", 1, "sell-order", "10.00")

    assert result["code"] == "CREATED_WITHOUT_PAY_URL"
    assert result["created"] is True
    assert result["order_id"] == "order-created"
    assert result["msg"] == "请刷新当前页面后重试"


def test_wechat_payment_lookup_auth_failure_is_created_pending(monkeypatch):
    monkeypatch.setattr("buff.buyer.jittered_sleep", lambda *_args, **_kwargs: None)
    session = FakeSession(
        FakeResponse({"code": "OK", "data": {"id": "wechat-order"}}),
        FakeResponse({"code": "LOGIN_REQUIRED"}, status_code=401),
    )
    buyer = BuffBuyer(
        "session=s; csrf_token=c",
        pay_method=PAY_METHOD_WECHAT,
        session=session,
        request_policy=no_wait_policy(),
    )

    result = buyer.lock_and_get_pay_url("csgo", 1, "sell-order", "10.00")

    assert result["code"] == "CREATED_WITHOUT_PAY_URL"
    assert result["created"] is True
    assert result["pay_type"] == "wechat"
    assert result["order_id"] == "wechat-order"
    assert [(method, url) for method, url, _ in session.calls] == [
        ("POST", API_BUY),
        ("GET", API_WX_PAY_QRCODE),
    ]


def test_lock_business_rejection_is_known_not_created():
    # BUFF 对写请求的明确业务拒绝（非 OK 且含错误原因）不可能是「已创建」，
    # 必须带 created=False 以便调用方跳过本件继续，而不是按未知结果停机。
    session = FakeSession(
        FakeResponse({"code": "FAIL", "error": "该饰品暂不支持此支付方式"})
    )
    buyer = BuffBuyer(
        "session=s; csrf_token=c", session=session, request_policy=no_wait_policy()
    )

    result = buyer.lock_and_get_pay_url("csgo", 1, "sell-order", "10.00")

    assert result["success"] is False
    assert result["created"] is False
    assert result["msg"] == "该饰品暂不支持此支付方式"
    assert len(session.calls) == 1


def test_created_order_with_valid_payment_url_keeps_success_contract():
    session = FakeSession(
        FakeResponse({"code": "OK", "data": {"id": "paid-order"}}),
        FakeResponse({"code": "OK", "data": {"url": " https://pay.example/1 "}}),
    )
    buyer = BuffBuyer(
        "session=s; csrf_token=c",
        session=session,
        request_policy=no_wait_policy(),
    )

    assert buyer.lock_and_get_pay_url("csgo", 1, "sell-order", "10.00") == {
        "success": True,
        "pay_url": "https://pay.example/1",
        "pay_type": "alipay",
        "order_id": "paid-order",
    }


@pytest.mark.parametrize(
    ("item", "expected"),
    [
        # 直接付款证据：buyer_pay_time 在扣款完成后立即写入
        pytest.param(
            {
                "mode": 2,
                "state": "PAYING",
                "progress": 102,
                "pay_expire_time": "2026-10-13 16:06:56",
                "pay_expire_timeout": 200,
                "buyer_pay_time": 1791878509,
            },
            BUY_ORDER_PAY_PAID,
            id="buyer_pay_time_written",
        ),
        # 状态证据：真实免密订单观测到 mode=5/state=DELIVERING/progress=202
        pytest.param(
            {"mode": 5, "state": "DELIVERING", "progress": 202},
            BUY_ORDER_PAY_PAID,
            id="delivering_after_passwordless_deduction",
        ),
        pytest.param(
            {"mode": 2, "state": "TO_DELIVER", "progress": 302},
            BUY_ORDER_PAY_PAID,
            id="waiting_delivery",
        ),
        pytest.param(
            {"mode": 2, "state": "SUCCESS", "progress": 101},
            BUY_ORDER_PAY_PAID,
            id="trade_success",
        ),
        pytest.param(
            {"mode": 5, "state": "PAYING", "progress": 305},
            BUY_ORDER_PAY_PAID,
            id="mode5_progress_terminal",
        ),
        pytest.param(
            {
                "mode": 2,
                "state": "PAYING",
                "progress": 102,
                "pay_expire_time": "2026-10-13 16:06:56",
                "pay_expire_timeout": 200,
            },
            BUY_ORDER_PAY_PENDING,
            id="awaiting_payment",
        ),
        pytest.param(
            {"mode": 2, "state": "PAYING", "progress": 102, "buyer_pay_time": "0"},
            BUY_ORDER_PAY_PENDING,
            id="string_zero_pay_time_is_not_payment",
        ),
        pytest.param(
            {"mode": 2, "state": "FAIL", "progress": 104},
            BUY_ORDER_PAY_FAILED,
            id="payment_failed",
        ),
        pytest.param(
            {
                "mode": 2,
                "state": "PAYING",
                "progress": 102,
                "pay_expire_time": "2026-10-13 16:06:56",
                "pay_expire_timeout": -1,
            },
            BUY_ORDER_PAY_FAILED,
            id="payment_window_expired",
        ),
        pytest.param({}, BUY_ORDER_PAY_UNKNOWN, id="empty_item"),
    ],
)
def test_bill_order_pay_state_probe_follows_client_state_machine(item, expected):
    session = FakeSession(FakeResponse({"code": "OK", "data": {"items": [item]}}))
    buyer = BuffBuyer(
        "session=s; csrf_token=c", session=session, request_policy=no_wait_policy()
    )

    result = buyer.get_bill_order_pay_state("bill-1", "csgo")

    assert result["status"] == expected
    method, url, kwargs = session.calls[0]
    assert (method, url) == ("GET", API_BILL_ORDER_BATCH_INFO)
    assert kwargs["params"]["bill_orders"] == "bill-1"


def test_buyer_send_offer_posts_encrypted_session_and_order_ids():
    session = FakeSession(FakeResponse({"code": "OK", "data": {"num": 1}}))
    buyer = BuffBuyer(
        "session=s; csrf_token=c", session=session, request_policy=no_wait_policy()
    )

    result = buyer.buyer_send_offer("steamLoginSecure=x; sessionid=y", ["bill-1", "bill-1", ""])

    assert result["success"] is True
    method, url, kwargs = session.calls[0]
    assert (method, url) == ("POST", API_MANUAL_PLUS_BUYER_SEND_OFFER)
    assert kwargs["headers"]["X-Csrftoken"] == "c"
    body = json.loads(kwargs["data"])
    assert body["bill_orders"] == ["bill-1"]
    assert isinstance(body["buyer_info"], str) and body["buyer_info"]


def test_buyer_send_offer_reports_rejection_without_retrying():
    session = FakeSession(
        FakeResponse({"code": "Error", "error": "订单状态不允许发起报价"})
    )
    buyer = BuffBuyer(
        "session=s; csrf_token=c", session=session, request_policy=no_wait_policy()
    )

    result = buyer.buyer_send_offer("steamLoginSecure=x", ["bill-1"])

    assert result["success"] is False
    assert "不允许" in result["error"]
    assert len(session.calls) == 1


def test_buyer_send_offer_requires_session_and_order_ids():
    session = FakeSession()
    buyer = BuffBuyer(
        "session=s; csrf_token=c", session=session, request_policy=no_wait_policy()
    )

    assert buyer.buyer_send_offer("   ", ["bill-1"])["success"] is False
    assert buyer.buyer_send_offer("steamLoginSecure=x", [])["success"] is False
    assert session.calls == []


def test_buyer_send_offer_transport_failure_stays_write_unknown():
    session = FakeSession(requests.ConnectionError("socket closed"))
    buyer = BuffBuyer(
        "session=s; csrf_token=c", session=session, request_policy=no_wait_policy()
    )

    with pytest.raises(BuffWriteResultUnknown):
        buyer.buyer_send_offer("steamLoginSecure=x", ["bill-1"])


def test_bill_order_pay_state_probe_is_unknown_for_unusable_responses():
    session = FakeSession(
        FakeResponse({"code": "SYSTEM_BUSY", "error": "系统繁忙"}),
        FakeResponse({"code": "OK", "data": "not-a-dict"}),
        FakeResponse({"code": "OK", "data": {"items": []}}),
        FakeResponse({"code": "OK", "data": {"items": [{"mode": 2}, {"mode": 2}]}}),
        requests.ConnectionError("socket closed"),
    )
    buyer = BuffBuyer(
        "session=s; csrf_token=c", session=session, request_policy=no_wait_policy()
    )

    for _ in range(5):
        assert buyer.get_bill_order_pay_state("bill-1")["status"] == BUY_ORDER_PAY_UNKNOWN


def test_login_redirect_is_auth_expired_and_verification_redirect_is_fused():
    login = FakeResponse(
        {"code": "OK"},
        url="https://buff.163.com/account/login",
        history=[
            FakeResponse(
                None,
                status_code=302,
                headers={"Location": "/account/login"},
                url="https://buff.163.com/api/history",
            )
        ],
    )
    login_session = FakeSession(login)
    login_buyer = BuffBuyer(
        "session=login", session=login_session, request_policy=no_wait_policy()
    )
    with pytest.raises(BuffAuthExpired):
        login_buyer._make_request("GET", "https://buff.163.com/api/history")

    verify = FakeResponse(
        {"code": "OK"},
        url="https://buff.163.com/verify",
        history=[
            FakeResponse(
                None,
                status_code=302,
                headers={"Location": "/verify"},
                url="https://buff.163.com/api/history",
            )
        ],
    )
    verify_session = FakeSession(verify)
    verify_buyer = BuffBuyer(
        "session=verify", session=verify_session, request_policy=no_wait_policy()
    )
    with pytest.raises(BuffVerificationRequired):
        verify_buyer._make_request("GET", "https://buff.163.com/api/history")
    with pytest.raises(BuffVerificationRequired):
        verify_buyer._make_request("GET", "https://buff.163.com/api/again")
    assert len(verify_session.calls) == 1


def test_unknown_html_read_fails_closed():
    session = FakeSession(
        FakeResponse(
            None,
            headers={"Content-Type": "text/html; charset=utf-8"},
            raw_text="<!doctype html><html><body>gateway page</body></html>",
        )
    )
    buyer = BuffBuyer(
        "session=html", session=session, request_policy=no_wait_policy()
    )
    with pytest.raises(BuffVerificationRequired):
        buyer._make_request("GET", "https://buff.163.com/api/read")


def test_initial_cookie_is_domain_scoped_and_expiration_removes_it():
    buyer = BuffBuyer(
        "session=old; csrf_token=c", request_policy=no_wait_policy()
    )
    try:
        session_cookie = next(
            cookie for cookie in buyer.session.cookies if cookie.name == "session"
        )
        assert session_cookie.domain == "buff.163.com"
        assert session_cookie.path == "/"
        assert session_cookie.secure is True

        expired = RequestsCookieJar()
        expired.set_cookie(
            create_cookie(
                "session",
                "",
                domain="buff.163.com",
                path="/",
                expires=1,
            )
        )
        buyer._merge_response_cookies(expired)
        assert "session=" not in buyer.export_cookie_string()
    finally:
        buyer.close()


def test_verify_session_is_one_lightweight_get_and_trade_poll_uses_same_session():
    session = FakeSession(
        FakeResponse({"code": "OK", "data": {"items": []}}),
        FakeResponse({"code": "OK", "data": [{"tradeofferid": "1"}]}),
    )
    buyer = BuffBuyer(
        "session=s; csrf_token=c", session=session, request_policy=no_wait_policy()
    )

    assert buyer.verify_session() is True
    assert buyer.get_steam_trades() == [{"tradeofferid": "1"}]
    assert [call[0] for call in session.calls] == ["GET", "GET"]
    assert session.calls[0][1] == API_HISTORY
    assert session.calls[0][2]["params"]["page_size"] == "10"
    assert "state" not in session.calls[0][2]["params"]
    assert session.calls[1][1] == API_STEAM_TRADE
    assert "to_receive" in session.calls[1][2]["headers"]["Referer"]


def test_cookie_persistence_failure_never_masks_operation_result_or_exception():
    from app.services.buff_client import BuffClient

    def persistence_failure(_cookies, _user_agent):
        raise OSError("disk full")

    class Buyer:
        user_agent = "Browser/1"

        def __init__(self, failure=None):
            self.failure = failure

        def get_sell_orders(self, _goods_id, _game):
            if self.failure:
                raise self.failure
            return [{"id": "ok"}]

        def export_cookie_string(self):
            return "session=rotated"

        def close(self):
            pass

    client = BuffClient(
        "session=old", credentials_update_callback=persistence_failure
    )
    client._buyer.close()
    client._buyer = Buyer()
    assert client.get_sell_orders(1) == [{"id": "ok"}]

    original = BuffRateLimited(30)
    client._buyer = Buyer(original)
    with pytest.raises(BuffRateLimited) as exc_info:
        client.get_sell_orders(1)
    assert exc_info.value is original
