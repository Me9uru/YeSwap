"""BUFF browser authentication and opt-in session keep-alive."""
from utils.paths import DATA_DIR

import json
import threading
import time
from typing import Any, Callable, Optional

from app.configuration.repository import get_buff_credentials, update_buff_creds
from app.runtime.state import (
    get_pending_payment,
    get_status,
    log,
    set_buff_auth_expired,
    set_buff_verification_required,
)
from app.runtime_env import get_browser_channel
from app.services.buff_browser_probe import (
    _response_has_challenge,
    detect_buff_challenge,
    read_buff_browser_user_agent,
)


_buff_auth_lock = threading.RLock()
_buff_auto_relogin_last_success = 0.0
BUFF_ACCOUNT_ID = "default"
BUFF_ORIGIN = "https://buff.163.com/"
BUFF_BROWSER_LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    # The authenticated requests.Session deliberately ignores environment and
    # OS proxies.  Force the BUFF login profile onto the same direct egress.
    "--no-proxy-server",
]

_CHECKOUT_CREDENTIAL_FREEZE_STEPS = frozenset(
    {
        "CHECKOUT_PENDING",
        "BUFF_WRITE_RESULT_UNKNOWN",
        "BUFF_ORDER_CREATED_PENDING",
    }
)


def get_buff_auth_lock() -> threading.RLock:
    """Return the process-wide lock guarding every BUFF credential writer.

    The lock is re-entrant because a browser login holds it for the profile's
    lifetime and then calls the credential update path before releasing it.
    """

    return _buff_auth_lock


def buff_credential_replacement_block_reason() -> str:
    """Return why BUFF credentials must stay frozen during checkout.

    The payment prompt is held in memory while terminal unknown/pending states
    require reconciliation.  Replacing the session in either case could make a
    subsequent lookup or finalization run against a different BUFF account.
    """

    try:
        from app.runtime.pipeline_control import is_shutdown_pending
        from app.services.buff_checkout_guard import get_unresolved_checkout

        if is_shutdown_pending():
            return "应用正在重置数据并等待退出，BUFF 凭证已冻结。"
        if get_unresolved_checkout() is not None:
            return (
                "当前存在跨重启保留的未对账 BUFF checkout，凭证已冻结；"
                "请先核对订单并显式解除对账门禁。"
            )
        status = get_status() or {}
        step = str(status.get("step") or "")
        if (
            status.get("status") == "running"
            or get_pending_payment()
            or step in _CHECKOUT_CREDENTIAL_FREEZE_STEPS
        ):
            return (
                "BUFF 流水线正在运行，或存在待付款/待对账订单，凭证已冻结；"
                "请先停止流水线并完成或人工核对当前订单。"
            )
    except Exception as exc:
        log(
            f"buff_auth: 无法确认结账状态，拒绝替换凭证: {exc}",
            "warn",
            category="buff",
        )
        return "无法确认当前 BUFF 结账状态，已拒绝替换凭证。"
    return ""


def browser_buff_verification_allowed(
    account_id: str = BUFF_ACCOUNT_ID,
) -> tuple[bool, str, str]:
    """Allow interactive verification except during an active 429 cooldown."""

    from buff import BuffRateLimited, BuffRequestBlocked, get_global_policy
    from buff.request_policy import account_fingerprint

    account_key = account_fingerprint({}, account_id=account_id)
    try:
        get_global_policy().raise_if_blocked(account_key)
    except BuffRateLimited as exc:
        return False, "rate_limited", str(exc)
    except BuffRequestBlocked:
        # Risk/verification circuits are exactly what an interactive browser is
        # expected to resolve.  They remain open until verification succeeds.
        return True, "verification_required", ""
    return True, "ok", ""


def _global_policy_and_account_key(
    account_id: str = BUFF_ACCOUNT_ID,
) -> tuple[Any, str]:
    from buff import get_global_policy
    from buff.request_policy import account_fingerprint

    return (
        get_global_policy(),
        account_fingerprint({}, account_id=account_id),
    )


def _evaluate_browser_verification_with_global_pacing(
    page: Any,
    script: str,
) -> Any:
    """Run the browser API probe in the policy's verification slot."""

    policy, account_key = _global_policy_and_account_key()
    with policy.verification_slot(account_key):
        return page.evaluate(script)


def _clear_account_circuit_preserving_pacing(policy: Any, account_key: str) -> None:
    """Clear one circuit without erasing the last global request timestamp."""
    # BuffRequestPolicy.clear() preserves pacing and resets an invalid-file
    # marker only after explicit browser verification.
    policy.clear(account_key)


def _trip_browser_verification(
    *,
    status_code: int = 0,
    message: str,
    retry_after: Optional[str] = None,
    rate_limited: bool = False,
) -> str:
    """Synchronize a browser validation failure into the global circuit."""

    from buff.request_policy import DEFAULT_RATE_LIMIT_SECONDS
    from utils.delay import parse_retry_after

    policy, account_key = _global_policy_and_account_key()
    if rate_limited or status_code == 429:
        delay = parse_retry_after(retry_after, default=DEFAULT_RATE_LIMIT_SECONDS)
        policy.trip_rate_limit(account_key, delay, message)
        return "rate_limited"
    if status_code in (403, 412):
        policy.trip_risk_control(
            account_key,
            status_code=status_code,
            message=message,
        )
        return "verification"
    policy.trip_verification(account_key, message)
    return "verification"


def clear_buff_request_policy_after_verification(
    account_id: str = BUFF_ACCOUNT_ID,
) -> bool:
    """Clear only one verified account's transport circuit."""

    try:
        from buff import (
            BuffRateLimited,
            BuffRequestBlocked,
            get_global_policy,
        )
        from buff.request_policy import account_fingerprint

        account_key = account_fingerprint({}, account_id=account_id)
        policy = get_global_policy()
        try:
            policy.raise_if_blocked(account_key)
        except BuffRateLimited as exc:
            log(
                f"buff_relogin: 账号仍在服务器冷却期，保留熔断: {exc}",
                "warn",
                category="buff",
            )
            return False
        except BuffRequestBlocked:
            # Browser/manual verification may clear verification and risk-control
            # circuits, but never an unexpired server cooldown.
            pass
        _clear_account_circuit_preserving_pacing(policy, account_key)
        return True
    except Exception as exc:
        log(f"buff_relogin: 重置请求策略失败 {exc}", "warn", category="buff")
        return False


def verify_buff_browser_session(page: Any) -> tuple[bool, str, str]:
    """Verify authentication with one small read-only request in the browser.

    A ``session`` cookie alone is not proof of a usable session: stale cookies
    and risk-control pages can retain it.  This endpoint is authenticated and
    requests only the smallest BUFF-supported history page, so an ``OK``
    response proves the profile can actually use the account without generating
    a write operation.
    """

    challenge = detect_buff_challenge(page)
    if challenge:
        _trip_browser_verification(message=challenge)
        return False, "verification", challenge

    script = """
        async () => {
          // Keep this probe independent of BUFF's changing order-state enum.
          // page_size=10 is currently accepted while page_size=1 is rejected.
          const url = "/api/market/buy_order/history?game=csgo&page_num=1&page_size=10";
          const controller = new AbortController();
          const timeoutId = setTimeout(() => controller.abort(), 15000);
          try {
            const response = await fetch(url, {
              method: "GET",
              credentials: "include",
              cache: "no-store",
              signal: controller.signal,
              headers: {
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "X-Requested-With": "XMLHttpRequest"
              }
            });
            const body = await response.text();
            let parsed = null;
            try {
              const value = JSON.parse(body);
              if (value && typeof value === "object") {
                parsed = {
                  code: value.code || "",
                  error: value.error || "",
                  msg: value.msg || "",
                  message: value.message || ""
                };
              }
            } catch (_) {}
            return {
              status: response.status,
              url: response.url,
              contentType: response.headers.get("content-type") || "",
              retryAfter: response.headers.get("retry-after") || "",
              payload: parsed,
              body: body.slice(0, 8192),
              bodyLength: body.length,
              bodyPrefixCodes: Array.from(body.slice(0, 8)).map(ch => ch.charCodeAt(0))
            };
          } finally {
            clearTimeout(timeoutId);
          }
        }
    """
    try:
        result = _evaluate_browser_verification_with_global_pacing(page, script)
    except Exception as exc:
        from buff import BuffRateLimited

        if isinstance(exc, BuffRateLimited):
            return False, "rate_limited", str(exc)
        return False, "error", f"BUFF 会话在线验证失败: {str(exc)[:120]}"
    if not isinstance(result, dict):
        return False, "error", "BUFF 会话在线验证返回格式异常"

    try:
        status_code = int(result.get("status", 0) or 0)
    except (TypeError, ValueError):
        status_code = 0
    response_url = str(result.get("url") or "").lower()
    response_body = str(result.get("body") or "")
    retry_after = str(result.get("retryAfter") or "")

    if status_code == 429:
        message = "BUFF 在线验证触发服务器限流（HTTP 429）"
        _trip_browser_verification(
            status_code=status_code,
            message=message,
            retry_after=retry_after,
            rate_limited=True,
        )
        return False, "rate_limited", message
    if status_code in (403, 412):
        message = f"BUFF 在线验证触发安全检查（HTTP {status_code}）"
        _trip_browser_verification(
            status_code=status_code,
            message=message,
        )
        return False, "verification", message
    if any(
        marker in response_url
        for marker in ("/verify", "/captcha", "/challenge")
    ):
        message = "BUFF 在线验证被重定向到安全验证页面"
        _trip_browser_verification(message=message)
        return False, "verification", message
    if _response_has_challenge(response_body):
        message = (
            f"BUFF 在线验证返回安全验证内容（HTTP {status_code or '未知'}）"
        )
        _trip_browser_verification(message=message)
        return False, "verification", message
    if status_code == 401 or any(marker in response_url for marker in ("/login", "/account/login")):
        return False, "expired", "BUFF 登录状态已失效"

    payload = result.get("payload")
    if not isinstance(payload, dict):
        try:
            payload = json.loads(response_body)
        except (TypeError, ValueError, json.JSONDecodeError):
            payload = None
    if not isinstance(payload, dict):
        content_type = str(result.get("contentType") or "unknown").split(";", 1)[0]
        response_path = response_url.split("?", 1)[0]
        body_length = int(result.get("bodyLength", len(response_body)) or 0)
        prefix_codes = result.get("bodyPrefixCodes")
        if not isinstance(prefix_codes, list):
            prefix_codes = []
        return (
            False,
            "error",
            "BUFF 在线验证未返回 JSON"
            f"（HTTP {status_code or '未知'}，Content-Type {content_type}，"
            f"长度 {body_length}，前缀编码 {prefix_codes[:8]}，"
            f"路径 {response_path or '未知'}）",
        )
    code = str(payload.get("code") or "")
    message = str(payload.get("error") or payload.get("msg") or "")
    combined = f"{code} {message}".lower()
    if code.upper() == "OK":
        return True, "ok", "BUFF 会话在线验证通过"
    if any(marker in combined for marker in ("login", "unauthorized", "未登录", "登录失效")):
        return False, "expired", message or "BUFF 登录状态已失效"
    if any(
        marker in combined
        for marker in ("too many requests", "rate limit", "访问受限")
    ):
        reason = message or f"BUFF 返回限流状态: {code}"
        _trip_browser_verification(
            message=reason,
            retry_after=retry_after,
            rate_limited=True,
        )
        return False, "rate_limited", reason
    if _response_has_challenge(combined):
        reason = message or f"BUFF 返回安全检查状态: {code}"
        _trip_browser_verification(message=reason)
        return False, "verification", reason
    return False, "error", message or f"BUFF 在线验证未通过: {code or '未知状态'}"


def verify_manual_buff_credentials(
    cookies: str,
    user_agent: str = "",
    on_verified_cookies: Optional[Callable[[str], None]] = None,
) -> tuple[bool, str, str]:
    """Probe manually supplied credentials exactly once on the global policy.

    The route checks the circuit before calling this function, and the shared
    policy checks it again immediately before the request.  This keeps manual
    validation inside the same pacing and circuit state as trading traffic.
    """

    from buff import (
        BuffAuthExpired,
        BuffBuyer,
        BuffRateLimited,
        BuffRequestBlocked,
        BuffRiskControlTriggered,
        BuffVerificationRequired,
        get_global_policy,
    )

    buyer = BuffBuyer(
        cookies,
        user_agent=(user_agent or "").strip() or None,
        request_policy=get_global_policy(),
        account_id=BUFF_ACCOUNT_ID,
        # BUFF occasionally accepts the TLS connection promptly but delays the
        # authenticated history response.  This is a one-off, read-only
        # credential probe; give it more headroom without relaxing the normal
        # trading client's 10-second request timeout.
        request_timeout=30.0,
    )
    try:
        if buyer.verify_session():
            if on_verified_cookies is not None:
                on_verified_cookies(buyer.export_cookie_string())
            return True, "ok", "BUFF 手工 Cookie 在线验证通过"
        result = getattr(buyer, "last_session_verification_result", {}) or {}
        code = str(result.get("code") or "未知")[:80]
        reason = str(result.get("error") or "")[:160]
        detail = f"（BUFF 返回 {code}" + (f"：{reason}" if reason else "") + "）"
        return False, "invalid", f"BUFF 手工 Cookie 在线验证未通过{detail}"
    except BuffAuthExpired:
        return False, "expired", "BUFF 手工 Cookie 已失效或未登录"
    except BuffRateLimited as exc:
        from buff import get_global_policy
        from buff.request_policy import account_fingerprint

        get_global_policy().trip_rate_limit(
            account_fingerprint({}, account_id=BUFF_ACCOUNT_ID),
            exc.retry_after,
            str(exc),
        )
        return False, "rate_limited", str(exc) or "BUFF 当前仍处于请求冷却"
    except BuffRiskControlTriggered as exc:
        from buff import get_global_policy
        from buff.request_policy import account_fingerprint

        get_global_policy().trip_risk_control(
            account_fingerprint({}, account_id=BUFF_ACCOUNT_ID),
            status_code=exc.status_code,
            message=str(exc),
        )
        return False, "verification", str(exc) or "BUFF 要求完成安全验证"
    except BuffVerificationRequired as exc:
        from buff import get_global_policy
        from buff.request_policy import account_fingerprint

        get_global_policy().trip_verification(
            account_fingerprint({}, account_id=BUFF_ACCOUNT_ID),
            str(exc),
        )
        return False, "verification", str(exc) or "BUFF 要求完成安全验证"
    except BuffRequestBlocked as exc:
        return False, "blocked", str(exc) or "BUFF 请求被安全策略阻止"
    except Exception as exc:
        return False, "error", f"BUFF 手工 Cookie 验证失败: {str(exc)[:120]}"
    finally:
        buyer.close()


def manual_buff_probe_allowed(
    candidate_cookies: str,
    saved_cookies: str,
) -> tuple[bool, str, str]:
    """Prevent manual resubmission from bypassing an existing circuit."""

    from buff import (
        BuffRateLimited,
        BuffRequestBlocked,
        get_global_policy,
    )
    from buff.request_policy import account_fingerprint

    account_key = account_fingerprint({}, account_id=BUFF_ACCOUNT_ID)
    try:
        get_global_policy().raise_if_blocked(account_key)
        return True, "ok", ""
    except BuffRateLimited as exc:
        # A fresh Cookie is not permission to ignore a server-issued cooldown.
        return False, "rate_limited", str(exc)
    except BuffRequestBlocked as exc:
        # Session/device values are attacker-controlled input and cannot prove
        # that a risk/verification circuit belongs to another account.  Only an
        # interactive browser verification may close this circuit.
        return (
            False,
            "browser_verification_required",
            str(exc) or "BUFF 安全验证尚未完成，请使用浏览器完成验证。",
        )


def try_buff_auto_relogin() -> tuple[bool, str, str]:
    """Refresh a Playwright-managed BUFF session without profile races."""

    global _buff_auto_relogin_last_success
    lock = get_buff_auth_lock()
    if not lock.acquire(blocking=False):
        log("buff_relogin: 另一项 BUFF 认证任务正在进行，跳过", "info", category="buff")
        if time.time() - _buff_auto_relogin_last_success < 60:
            return True, "auto_ok", "另一项 BUFF 认证任务刚刚完成"
        return False, "busy", "另一项 BUFF 认证任务正在进行"
    try:
        from app.services.buff_checkout_guard import buff_activity_guard

        # Keep pipeline acknowledgement/start outside the complete browser
        # verification + credential-commit operation.
        with buff_activity_guard():
            return _try_buff_auto_relogin_impl()
    finally:
        lock.release()


def _try_buff_auto_relogin_impl() -> tuple[bool, str, str]:
    global _buff_auto_relogin_last_success

    cred = get_buff_credentials() or {}
    if not cred.get("cookies"):
        log("buff_relogin: 未保存凭证，无法保活", "warn", category="buff")
        return False, "no_creds", "未配置初始凭证，无法自动保活"

    checkout_block = buff_credential_replacement_block_reason()
    if checkout_block:
        return False, "checkout_pending", checkout_block

    # A manually copied cookie belongs to a different browser profile.  Opening
    # our fixed Playwright profile must never replace it with unrelated cookies.
    if str(cred.get("source") or "").lower() != "playwright":
        log(
            "buff_relogin: 当前凭证不是由 Playwright profile 管理，已跳过自动覆盖",
            "info",
            category="buff",
        )
        return False, "external_credentials", "手工 Cookie 不会被自动浏览器保活覆盖"

    probe_allowed, circuit_status, circuit_message = manual_buff_probe_allowed(
        str(cred.get("cookies") or ""),
        str(cred.get("cookies") or ""),
    )
    if not probe_allowed:
        log(
            f"buff_relogin: 请求熔断仍开放，跳过自动保活: {circuit_message}",
            "warn",
            category="buff",
        )
        return False, circuit_status, circuit_message

    profile_dir = DATA_DIR / "playwright_buff"
    profile_dir.mkdir(parents=True, exist_ok=True)
    log("buff_relogin: 开始验证并刷新浏览器会话", "info", category="buff")

    context = None
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as playwright:
            launch_options = {
                "headless": True,
                "args": BUFF_BROWSER_LAUNCH_ARGS,
            }
            browser_channel = get_browser_channel()
            if browser_channel:
                launch_options["channel"] = browser_channel
            saved_user_agent = str(cred.get("user_agent") or "").strip()
            if saved_user_agent:
                launch_options["user_agent"] = saved_user_agent
            context = playwright.chromium.launch_persistent_context(
                str(profile_dir),
                **launch_options,
            )
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(BUFF_ORIGIN, wait_until="domcontentloaded", timeout=30000)
            page.wait_for_timeout(3000)

            verified, status, message = verify_buff_browser_session(page)
            if not verified:
                if status == "verification":
                    set_buff_verification_required(True, message)
                elif status == "expired":
                    set_buff_auth_expired(True)
                log(f"buff_relogin: {message}，保留原凭证", "warn", category="buff")
                return False, status, message

            # Read again after validation so Set-Cookie refreshes are included.
            cookies = context.cookies([BUFF_ORIGIN])
            if not any(c.get("name") == "session" and c.get("value") for c in cookies):
                set_buff_auth_expired(True)
                log("buff_relogin: 浏览器 profile 中没有有效 session", "warn", category="buff")
                return False, "expired", "登录状态已失效，请在界面重新登录"
            checkout_block = buff_credential_replacement_block_reason()
            if checkout_block:
                return False, "checkout_pending", checkout_block
            cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in cookies if c.get("name"))
            user_agent = read_buff_browser_user_agent(page) or saved_user_agent
            update_buff_creds(cookie_str, user_agent=user_agent or None, source="playwright")
            if not clear_buff_request_policy_after_verification(BUFF_ACCOUNT_ID):
                return False, "policy_error", "BUFF 会话已验证，但请求熔断重置失败"
            set_buff_auth_expired(False)
            set_buff_verification_required(False)
            _buff_auto_relogin_last_success = time.time()
            log("buff_relogin: 会话验证及 Cookie 刷新成功", "info", category="buff")
            return True, "auto_ok", "BUFF 会话验证及刷新成功"
    except Exception as exc:
        log(f"buff_relogin: 异常 {exc}", "warn", category="buff")
        return False, "error", (str(exc)[:120] or "自动保活异常")
    finally:
        if context is not None:
            try:
                context.close()
            except Exception:
                pass
