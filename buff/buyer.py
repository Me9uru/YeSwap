import json
import time
import logging
import copy
from decimal import Decimal, ROUND_HALF_UP
from typing import Callable, Optional
from urllib.parse import urljoin

logger = logging.getLogger(__name__)
import requests
import urllib3
from requests.cookies import RequestsCookieJar

from .crypto import encrypt_buff_payload
from .request_policy import (
    BuffAuthExpired,
    BuffRateLimited,
    BuffRequestBlocked,
    BuffRequestPolicy,
    BuffRiskControlTriggered,
    BuffVerificationRequired,
    BuffWriteResultUnknown,
    DEFAULT_RATE_LIMIT_SECONDS,
    account_fingerprint,
    get_global_policy,
)
from utils.delay import jittered_sleep, parse_retry_after

BUFF_COOKIE_DOMAIN = "buff.163.com"
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def _is_auth_error(status_code: int, data: dict) -> bool:
    if status_code == 401:
        return True
    code = str(data.get("code", "")).lower()
    err_val = data.get("error") or data.get("msg") or ""
    msg = str(err_val).lower()
    if "login" in code or "login" in msg or "未登录" in msg or "登录" in msg:
        return True
    return False

def _is_verification_required(data: dict) -> bool:
    text = str(data.get("error") or data.get("msg") or data.get("message") or "").lower()
    code = str(data.get("code", "")).lower()
    haystack = f"{code} {text}"
    markers = (
        "页面已过期",
        "刷新当前页面",
        "人机验证",
        "captcha",
        "challenge",
        "risk",
        "访问过于频繁",
        "安全验证",
    )
    return any(marker in haystack for marker in markers)

def _is_rate_limited(status_code: int, data: dict) -> bool:
    if status_code == 429:
        return True
    code = str(data.get("code", "")).lower()
    text = str(data.get("error") or data.get("msg") or data.get("message") or "").lower()
    haystack = f"{code} {text}"
    return any(
        marker in haystack
        for marker in (
            "too many requests",
            "rate limit",
            "rate_limit",
            "cooling down",
            "请求过于频繁",
            "请求频繁",
            "操作频繁",
        )
    )

def _header_value(headers, name: str) -> str:
    if not headers:
        return ""
    direct = headers.get(name)
    if direct is not None:
        return str(direct)
    lowered = name.lower()
    for key, value in headers.items():
        if str(key).lower() == lowered:
            return str(value)
    return ""

def _response_navigation(response) -> tuple[list[str], bool]:
    urls = []
    history = list(getattr(response, "history", None) or [])
    for item in history:
        item_url = str(getattr(item, "url", "") or "")
        if item_url:
            urls.append(item_url)
        location = _header_value(getattr(item, "headers", {}) or {}, "Location")
        if location:
            urls.append(urljoin(item_url, location))
    final_url = str(getattr(response, "url", "") or "")
    if final_url:
        urls.append(final_url)
    status_code = int(getattr(response, "status_code", 0) or 0)
    location = _header_value(getattr(response, "headers", {}) or {}, "Location")
    if location:
        urls.append(urljoin(final_url, location))
    return urls, bool(history or 300 <= status_code < 400)

def _navigation_kind(response) -> tuple[str, bool]:
    urls, redirected = _response_navigation(response)
    haystack = " ".join(urls).lower()
    if any(marker in haystack for marker in ("/login", "/account/login", "passport")):
        return "login", redirected
    if any(marker in haystack for marker in ("/verify", "captcha", "challenge", "risk")):
        return "verification", redirected
    return "", redirected

def _looks_like_html(headers, text: str) -> bool:
    content_type = _header_value(headers, "Content-Type").lower()
    stripped = str(text or "").lstrip().lower()
    return "text/html" in content_type or stripped.startswith(("<!doctype html", "<html"))

def _html_looks_like_login(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(marker in lowered for marker in ("/login", "登录", "未登录", "sign in"))
# BUFF 的 49 是通用支付宝渠道；51 是「支付宝-信用卡花呗」独立渠道（含渠道
# 服务费），不能作为免密/普通支付宝支付的默认值（参考上游对 BUFF 购买预检
# 的实测结论）。
PAY_METHOD_ALIPAY = 49
PAY_METHOD_WECHAT = 6
API_HISTORY = "https://buff.163.com/api/market/buy_order/history"
API_SELL_ORDER = "https://buff.163.com/api/market/goods/sell_order"
API_GOODS = "https://buff.163.com/api/market/goods"
API_BUY = "https://buff.163.com/api/market/goods/buy"
API_PAGE_PAY = "https://buff.163.com/api/market/bill_order/page_pay"
API_WX_PAY_QRCODE = "https://buff.163.com/api/market/bill_order/wx_pay_qrcode"
API_BATCH_BUY_CREATE = "https://buff.163.com/api/market/goods/batch_buy/create"
API_BATCH_WX_PAY_QRCODE = "https://buff.163.com/api/market/goods/batch_buy/wx_pay_qrcode"
API_ASK_SELLER_SEND = "https://buff.163.com/api/market/bill_order/ask_seller_to_send_offer"
API_STEAM_TRADE = "https://buff.163.com/api/market/steam_trade"
API_BILL_ORDER_BATCH_INFO = "https://buff.163.com/api/market/bill_order/batch/info"
API_MANUAL_PLUS_BUYER_SEND_OFFER = (
    "https://buff.163.com/api/market/manual_plus/buyer_send_offer"
)
ASK_SELLER_MAX_ATTEMPTS = 3
ASK_SELLER_BETWEEN_REQUEST_SECONDS = 1.5
BUY_ORDER_PAY_PAID = "paid"
BUY_ORDER_PAY_FAILED = "failed"
BUY_ORDER_PAY_PENDING = "pending"
BUY_ORDER_PAY_UNKNOWN = "unknown"


def _order_int(value):
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _has_time_value(value) -> bool:
    if value is None or isinstance(value, bool):
        return False
    return str(value).strip() not in ("", "0", "None", "null")


def _is_passwordless_cashier_response(result) -> bool:
    """识别免密（自动扣款）订单的收银台响应。

    免密订单不会产出可用的支付链接：``page_pay`` 实测返回 code=OK，data 中
    只有 ``auto_pay`` / ``buff_pay_channel`` 渠道配置，没有 ``elements_v2`` /
    ``elements`` / ``url`` 等链接字段。判据刻意保守：两个渠道键必须同时出现
    且不含任何链接字段；字段集变化时退回原有重试行为，不会误判跳过付款。
    """
    if not isinstance(result, dict) or str(result.get("code") or "").strip() != "OK":
        return False
    data = result.get("data")
    if not isinstance(data, dict):
        return False
    if "auto_pay" not in data or "buff_pay_channel" not in data:
        return False
    return not any(data.get(field) for field in ("elements_v2", "elements", "url"))


def classify_bill_order_pay_state(item) -> str:
    """把 ``bill_order/batch/info`` 的单条订单归类为付款状态。

    判定基于两类证据（对真实免密订单的观测 + BUFF 网页客户端
    「订单创建后确认付款」的轮询规则）：

    - 直接付款证据：``buyer_pay_time`` 在扣款完成后立即写入（实测免密扣款
      在订单创建后数秒内完成）；
    - 状态证据：``state`` 为 TO_DELIVER/DELIVERING/SUCCESS，或各 mode 对应
      的 progress 终态（105/106、305/309、mode7 的 202）；``progress=104``
      与待付款窗口超时（progress=102 且 pay_expire_timeout<0）为付款失败。

    无法解析的响应一律返回 unknown，调用方必须保持人工对账门禁，不得把
    含糊响应推断为已支付。
    """

    if not isinstance(item, dict):
        return BUY_ORDER_PAY_UNKNOWN
    mode = _order_int(item.get("mode"))
    progress = _order_int(item.get("progress"))
    state = str(item.get("state") or "").strip().upper()
    if _has_time_value(item.get("buyer_pay_time")):
        return BUY_ORDER_PAY_PAID
    if (
        state in ("TO_DELIVER", "DELIVERING")
        or state == "SUCCESS"
        or progress in (105, 106)
        or (mode == 5 and progress in (305, 309))
        or (mode == 8 and progress in (305, 309))
        or (mode == 7 and progress == 202)
    ):
        return BUY_ORDER_PAY_PAID
    if progress == 104:
        return BUY_ORDER_PAY_FAILED
    if progress == 102 and item.get("pay_expire_time"):
        try:
            expired = float(item.get("pay_expire_timeout")) < 0
        except (TypeError, ValueError):
            expired = False
        if expired:
            return BUY_ORDER_PAY_FAILED
    if mode is None and progress is None and not state:
        return BUY_ORDER_PAY_UNKNOWN
    return BUY_ORDER_PAY_PENDING
from utils.cookies import parse_cookies as _parse_cookies
def _csrf(cookies_dict: dict) -> str:
    return cookies_dict.get("csrf_token", "").strip('"')

def _normalize_server_identifier(value) -> str:
    """Accept only scalar, non-empty IDs from successful write responses."""

    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return ""
    normalized = str(value).strip()
    return normalized if normalized and normalized != "0" else ""

def _cookie_jar_dict(cookie_jar: RequestsCookieJar) -> dict:
    """Select the most specific cookies applicable to BUFF API paths."""

    selected = {}
    for cookie in cookie_jar:
        if cookie.is_expired():
            continue
        domain = str(cookie.domain or "").lstrip(".").lower()
        if domain and domain != BUFF_COOKIE_DOMAIN:
            continue
        path = str(cookie.path or "/")
        if not "/api/".startswith(path.rstrip("/") + "/") and path != "/":
            continue
        priority = (len(path), 1 if domain == BUFF_COOKIE_DOMAIN else 0)
        previous = selected.get(cookie.name)
        if previous is None or priority >= previous[0]:
            selected[cookie.name] = (priority, cookie.value)
    return {name: value for name, (_, value) in selected.items()}

class BuffBuyer:
    def __init__(
        self,
        cookie_str: str,
        pay_method: int = PAY_METHOD_ALIPAY,
        use_ssl: bool = True,
        *,
        user_agent: Optional[str] = None,
        session: Optional[requests.Session] = None,
        request_policy: Optional[BuffRequestPolicy] = None,
        account_id: Optional[str] = None,
        request_timeout: float = 10.0,
    ):
        self.cookies_dict = _parse_cookies(cookie_str)
        self.csrf_token = _csrf(self.cookies_dict)
        self.pay_method = pay_method
        self.use_ssl = use_ssl
        self.session = session if session is not None else requests.Session()
        self._owns_session = session is None
        self._closed = False
        if self._owns_session:
            # Authenticated BUFF traffic must not silently switch exits through
            # HTTP_PROXY/HTTPS_PROXY or OS proxy settings between requests.
            self.session.trust_env = False
        for name, value in self.cookies_dict.items():
            for old_cookie in list(self.session.cookies):
                old_domain = str(old_cookie.domain or "").lstrip(".").lower()
                if (
                    old_cookie.name == name
                    and old_domain in {"", BUFF_COOKIE_DOMAIN}
                    and (old_cookie.path or "/") == "/"
                ):
                    self.session.cookies.clear(
                        domain=old_cookie.domain,
                        path=old_cookie.path,
                        name=old_cookie.name,
                    )
            self.session.cookies.set(
                name,
                value,
                domain=BUFF_COOKIE_DOMAIN,
                path="/",
                secure=True,
            )
        self.request_policy = (
            request_policy if request_policy is not None else get_global_policy()
        )
        self.request_timeout = max(1.0, float(request_timeout))
        # Kept deliberately small and credential-free.  When a checkout was
        # created but its QR-code lookup fails, this gives operators enough
        # context to distinguish a transient empty response from an API or
        # transport problem without persisting request headers, cookies or a
        # payment URL.
        self.last_payment_lookup_diagnostic: dict = {}
        self._last_response_meta: dict = {}
        self._last_payment_lookup_response: dict = {}
        self.account_key = account_fingerprint(self.cookies_dict, account_id)
        self._user_agent = user_agent or DEFAULT_USER_AGENT
        self.headers = {
            "Host": "buff.163.com",
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "X-Csrftoken": self.csrf_token,
            "User-Agent": self._user_agent,
            "Content-Type": "application/json",
            "Accept-Language": "zh-CN,zh;q=0.9",
        }
        self.session.headers.update({"User-Agent": self.headers["User-Agent"]})

    @property
    def user_agent(self) -> str:
        """The stable UA bound to this authenticated session."""

        return self._user_agent

    def export_cookie_string(self) -> str:
        """Return the session's latest cookies after any Set-Cookie rotations."""

        self._sync_session_cookies()
        return "; ".join(
            f"{name}={value}" for name, value in self.cookies_dict.items()
        )

    def close(self) -> None:
        """Release an internally-created Session during credential hot reload."""

        if self._owns_session and not self._closed:
            self.session.close()
            self._closed = True

    def _sync_session_cookies(self) -> None:
        self.session.cookies.clear_expired_cookies()
        self.cookies_dict = _cookie_jar_dict(self.session.cookies)
        self.csrf_token = _csrf(self.cookies_dict)

    def _merge_response_cookies(self, response_cookies: RequestsCookieJar) -> None:
        """Apply rotated cookies without retaining an older duplicate by domain."""

        for response_cookie in list(response_cookies):
            new_cookie = copy.copy(response_cookie)
            if not new_cookie.domain:
                new_cookie.domain = BUFF_COOKIE_DOMAIN
                new_cookie.domain_specified = True
                new_cookie.domain_initial_dot = False
            if not new_cookie.path:
                new_cookie.path = "/"
                new_cookie.path_specified = True
            new_domain = str(new_cookie.domain or "").lstrip(".").lower()
            # Collapse only equivalent root-scoped BUFF cookies.  Legitimate
            # same-name cookies on a narrower path remain untouched.
            for old_cookie in list(self.session.cookies):
                old_domain = str(old_cookie.domain or "").lstrip(".").lower()
                if (
                    old_cookie.name == new_cookie.name
                    and old_domain == new_domain == BUFF_COOKIE_DOMAIN
                    and (old_cookie.path or "/") == (new_cookie.path or "/")
                ):
                    self.session.cookies.clear(
                        domain=old_cookie.domain,
                        path=old_cookie.path,
                        name=old_cookie.name,
                    )
            if not new_cookie.is_expired():
                self.session.cookies.set_cookie(new_cookie)

    def _make_request(self, method: str, url: str, **kwargs) -> dict:
        method = method.upper()
        headers_override = kwargs.pop("headers", None)
        verify = kwargs.pop("verify", self.use_ssl)
        timeout = kwargs.pop("timeout", self.request_timeout)
        is_write = method not in _SAFE_METHODS

        with self.request_policy.request_slot(self.account_key):
            # Cookie state and the outbound CSRF header must be captured only
            # after this account owns the request slot.  Otherwise a waiting
            # writer can retain a token rotated by the preceding response.
            self._sync_session_cookies()
            h = self.headers.copy()
            if method in _SAFE_METHODS:
                h.pop("Content-Type", None)
            if headers_override:
                for key, value in headers_override.items():
                    if value is None:
                        h.pop(key, None)
                    else:
                        h[key] = value
            if is_write:
                if not self.csrf_token:
                    raise BuffAuthExpired(
                        "BUFF csrf_token 缺失，写请求已在本地阻止"
                    )
                self.headers["X-Csrftoken"] = self.csrf_token
                h["X-Csrftoken"] = self.csrf_token

            try:
                request_started_at = time.monotonic()
                r = self.session.request(
                    method,
                    url,
                    headers=h,
                    verify=verify,
                    timeout=timeout,
                    **kwargs,
                )
            except requests.RequestException as exc:
                if is_write:
                    raise BuffWriteResultUnknown(
                        "BUFF 写请求发生网络异常，结果未知，禁止自动重试",
                        method=method,
                        url=url,
                    ) from exc
                raise

            response_cookies = getattr(r, "cookies", None)
            if response_cookies is not None:
                self._merge_response_cookies(response_cookies)
            self._sync_session_cookies()
            status_code = int(getattr(r, "status_code", 0) or 0)
            response_text = str(getattr(r, "text", "") or "")
            response_headers = getattr(r, "headers", {}) or {}
            json_ok = False
            data = {}
            try:
                parsed = r.json() if response_text else None
                if isinstance(parsed, dict):
                    data = parsed
                    json_ok = True
            except ValueError:
                pass

            self._last_response_meta = {
                "method": method,
                "status_code": status_code,
                "elapsed_ms": round((time.monotonic() - request_started_at) * 1000),
                "json_ok": json_ok,
            }

            if status_code in {403, 412}:
                message = (
                    data.get("error")
                    or data.get("msg")
                    or data.get("message")
                    or f"BUFF 风控响应 HTTP {status_code}"
                )
                self.request_policy.trip_risk_control(
                    self.account_key, status_code=status_code, message=str(message)
                )
                if is_write:
                    raise BuffWriteResultUnknown(
                        f"BUFF 写请求触发风控响应 HTTP {status_code}，结果未知，禁止自动重试",
                        method=method,
                        url=url,
                        status_code=status_code,
                    )
                raise BuffRiskControlTriggered(str(message), status_code=status_code)

            if _is_rate_limited(status_code, data):
                retry_after = parse_retry_after(
                    _header_value(response_headers, "Retry-After") or None,
                    default=DEFAULT_RATE_LIMIT_SECONDS,
                )
                message = data.get("error") or data.get("msg") or "BUFF 请求过于频繁"
                self.request_policy.trip_rate_limit(
                    self.account_key, retry_after, str(message)
                )
                if is_write:
                    raise BuffWriteResultUnknown(
                        "BUFF 写请求触发限流响应，结果未知，禁止自动重试",
                        method=method,
                        url=url,
                        status_code=status_code,
                    )
                raise BuffRateLimited(retry_after, str(message))

            navigation_kind, redirected = _navigation_kind(r)
            if navigation_kind == "login":
                if is_write:
                    raise BuffWriteResultUnknown(
                        "BUFF 写请求已重定向到登录页，结果未知，禁止自动重试",
                        method=method,
                        url=url,
                        status_code=status_code,
                    )
                raise BuffAuthExpired("BUFF API 已重定向到登录页")
            if navigation_kind == "verification" or redirected:
                message = "BUFF API 重定向到安全验证页" if navigation_kind else "BUFF API 出现非预期重定向"
                self.request_policy.trip_verification(self.account_key, message)
                if is_write:
                    raise BuffWriteResultUnknown(
                        f"{message}，写请求结果未知，禁止自动重试",
                        method=method,
                        url=url,
                        status_code=status_code,
                    )
                raise BuffVerificationRequired(message)

            if _is_auth_error(status_code, data):
                if is_write:
                    raise BuffWriteResultUnknown(
                        "BUFF 写请求返回登录失效状态，结果未知，禁止自动重试",
                        method=method,
                        url=url,
                        status_code=status_code,
                    )
                raise BuffAuthExpired()
            if _is_verification_required(data) or _is_verification_required(
                {"message": response_text}
            ):
                msg = (
                    data.get("error")
                    or data.get("msg")
                    or data.get("message")
                    or "Buff 需要刷新页面或完成人机验证"
                )
                self.request_policy.trip_verification(self.account_key, str(msg))
                if is_write:
                    raise BuffWriteResultUnknown(
                        "BUFF 写请求返回安全验证状态，结果未知，禁止自动重试",
                        method=method,
                        url=url,
                        status_code=status_code,
                    )
                raise BuffVerificationRequired(str(msg))

            if is_write and status_code >= 500:
                raise BuffWriteResultUnknown(
                    f"BUFF 写请求返回 HTTP {status_code}，结果未知，禁止自动重试",
                    method=method,
                    url=url,
                    status_code=status_code,
                )

            if _looks_like_html(response_headers, response_text):
                if _html_looks_like_login(response_text):
                    if is_write:
                        raise BuffWriteResultUnknown(
                            "BUFF 写请求返回登录页面，结果未知，禁止自动重试",
                            method=method,
                            url=url,
                            status_code=status_code,
                        )
                    raise BuffAuthExpired("BUFF API 返回登录页面")
                if is_write:
                    message = "BUFF 写请求返回非预期 HTML，已停止后续请求"
                    self.request_policy.trip_verification(
                        self.account_key, message
                    )
                    raise BuffWriteResultUnknown(
                        "BUFF 写请求返回 HTML，结果未知，禁止自动重试",
                        method=method,
                        url=url,
                        status_code=status_code,
                    )
                message = "BUFF API 返回非预期 HTML，已停止后续请求"
                self.request_policy.trip_verification(self.account_key, message)
                raise BuffVerificationRequired(message)

            if is_write and not json_ok:
                raise BuffWriteResultUnknown(
                    "BUFF 写请求未返回有效 JSON，结果未知，禁止自动重试",
                    method=method,
                    url=url,
                    status_code=status_code,
                )

            if not json_ok:
                return {
                    "code": "HTTP_" + str(status_code),
                    "error": f"接口返回非预期内容 (Status: {status_code})",
                }
            return data

    def verify_session(self, game: str = "csgo") -> bool:
        """Verify the current credentials with exactly one lightweight GET."""

        params = {
            "game": game,
            "page_num": "1",
            # BUFF currently rejects page_size=1. Ten is the smallest accepted
            # value and needs no volatile order-state filter.
            "page_size": "10",
            "_": str(int(time.time() * 1000)),
        }
        response = self._make_request("GET", API_HISTORY, params=params)
        # Keep only the server's already-sanitised JSON result for the caller
        # to explain a failed manual-login probe.  No request headers or
        # Cookie values are retained here.
        self.last_session_verification_result = {
            "code": response.get("code"),
            "error": response.get("error") or response.get("msg") or response.get("message"),
        }
        return response.get("code") == "OK"

    def get_steam_trades(self) -> Optional[list]:
        """Return pending BUFF-to-Steam trade records through this session."""

        headers = {
            "Referer": "https://buff.163.com/market/buy_order/to_receive?game=csgo"
        }
        params = {"_": str(int(time.time() * 1000))}
        try:
            response = self._make_request(
                "GET", API_STEAM_TRADE, params=params, headers=headers
            )
            if response.get("code") != "OK":
                return None
            data = response.get("data")
            return data if isinstance(data, list) else None
        except (BuffAuthExpired, BuffRequestBlocked):
            raise
        except Exception:
            return None

    def get_bill_order_pay_state(self, order_id: str, game: str = "csgo") -> dict:
        """只读查询一个买单的付款/发货状态。

        免密支付（网易宝快捷代扣）在订单创建后由 BUFF 自动扣款，此时不会
        返回支付链接；网页客户端在「订单创建后的付款确认」阶段轮询
        ``/api/market/bill_order/batch/info`` 判定结果，本方法使用同一接口
        与同一判定规则。只读、可安全轮询，不会创建、修改或重试任何订单写
        请求；返回值包含 ``status`` 及用于日志诊断的非敏感字段。
        """

        params = {
            "bill_orders": str(order_id),
            "_": str(int(time.time() * 1000)),
        }
        h = {
            "Referer": f"https://buff.163.com/market/buy_order/history?game={game}",
        }
        try:
            res = self._make_request(
                "GET", API_BILL_ORDER_BATCH_INFO, params=params, headers=h
            )
        except (BuffAuthExpired, BuffRequestBlocked):
            raise
        except Exception:
            return {"status": BUY_ORDER_PAY_UNKNOWN}
        data = res.get("data") if isinstance(res, dict) and res.get("code") == "OK" else None
        items = data.get("items") if isinstance(data, dict) else None
        if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], dict):
            return {"status": BUY_ORDER_PAY_UNKNOWN}
        item = items[0]
        return {
            "status": classify_bill_order_pay_state(item),
            "mode": item.get("mode"),
            "state": item.get("state"),
            "progress": item.get("progress"),
            "pay_expire_timeout": item.get("pay_expire_timeout"),
            "buyer_pay_time": item.get("buyer_pay_time"),
            "has_sent_offer": item.get("has_sent_offer"),
            "buyer_send_offer_timeout": item.get("buyer_send_offer_timeout"),
        }

    def get_sell_orders(self, goods_id: int, game: str = "csgo") -> Optional[list]:
        params = {
            "game": str(game),
            "goods_id": str(goods_id),
            "page_num": "1",
            "sort_by": "default",
            "mode": "",
            "allow_tradable_cooldown": "1",
            "_": str(int(time.time() * 1000)),
        }
        h = {"Referer": f"https://buff.163.com/goods/{goods_id}"}
        try:
            data = self._make_request("GET", API_SELL_ORDER, params=params, headers=h)
            if data.get("code") != "OK":
                logger.warning(
                    "BUFF 卖单接口返回非 OK，停止本轮请求: %s",
                    data.get("code", "UNKNOWN"),
                )
                return None
            return data.get("data", {}).get("items", []) or None
        except (BuffAuthExpired, BuffRequestBlocked):
            raise
        except Exception:
            return None
    def get_goods_steam_price_cny(self, search_name: str, game: str = "csgo") -> Optional[float]:
        params = {
            "game": game,
            "page_num": "1",
            "search": search_name.strip(),
            "tab": "selling",
            "_": str(int(time.time() * 1000)),
        }
        h = {"Referer": "https://buff.163.com/market/csgo"}
        try:
            data = self._make_request("GET", API_GOODS, params=params, headers=h)
            if data.get("code") != "OK":
                return None
            items = data.get("data", {}).get("items", [])
            if not items:
                return None
            goods_info = items[0].get("goods_info") or {}
            raw = goods_info.get("steam_price_cny")
            if raw is None:
                return None
            return float(raw)
        except (BuffAuthExpired, BuffRequestBlocked):
            raise
        except (ValueError, TypeError, KeyError):
            return None
        except Exception:
            return None
    def lock_and_get_pay_url(
        self,
        game: str,
        goods_id: int,
        sell_order_id: str,
        price: str,
        *,
        on_created: Optional[Callable[[str], None]] = None,
    ) -> dict:
        payload = {
            "game": game,
            "goods_id": str(goods_id),
            "sell_order_id": sell_order_id,
            "price": price,
            "pay_method": self.pay_method,
            "allow_tradable_cooldown": 0,
            "token": "",
            "cdkey_id": "",
            "hide_non_epay": True,
        }
        if self.pay_method == PAY_METHOD_ALIPAY:
            payload["steamid"] = None
        h = {"Referer": f"https://buff.163.com/goods/{goods_id}?from=market"}
        try:
            res = self._make_request("POST", API_BUY, headers=h, data=json.dumps(payload))
            if res.get("code") != "OK":
                err_msg = res.get("error") or res.get("msg") or f"接口代码非 OK (Code: {res.get('code', 'N/A')})"
                msg_str = str(err_msg)
                if "Cooling Down" in msg_str:
                    return {"success": False, "code": "COOLING_DOWN"}
                # BUFF 对写请求返回了明确的业务拒绝（如「该饰品暂不支持此
                # 支付方式」），订单未创建：标记 created=False，调用方按
                # 已知失败跳过本件并继续，而不是按结果未知停机。传输失败、
                # 非 JSON 响应与风控重定向仍由 _make_request 以
                # BuffWriteResultUnknown 抛出，保持未知结果门禁。
                return {
                    "success": False,
                    "code": "FAIL",
                    "created": False,
                    "msg": err_msg,
                }
            data = res.get("data")
            if not isinstance(data, dict):
                raise BuffWriteResultUnknown(
                    "BUFF 锁单返回成功但 data 格式异常，结果未知",
                    method="POST",
                    url=API_BUY,
                )
            new_order_id = _normalize_server_identifier(data.get("id"))
            if not new_order_id:
                raise BuffWriteResultUnknown(
                    "BUFF 锁单返回成功但缺少订单号，结果未知",
                    method="POST",
                    url=API_BUY,
                )
            if on_created is not None:
                try:
                    on_created(new_order_id)
                except Exception as exc:
                    error = BuffWriteResultUnknown(
                        "BUFF 订单已创建，但订单号未能写入本地对账门禁",
                        method="POST",
                        url=API_BUY,
                    )
                    error.order_id = new_order_id
                    raise error from exc
            pay_type = "wechat" if self.pay_method == PAY_METHOD_WECHAT else "alipay"
            pay_url = self._fetch_created_order_payment_url(game, new_order_id)
            if not isinstance(pay_url, str) or not pay_url.strip():
                pending_result = {
                    "success": False,
                    "code": "CREATED_WITHOUT_PAY_URL",
                    "created": True,
                    "pay_url": None,
                    "pay_type": pay_type,
                    "order_id": new_order_id,
                }
                diagnostic_message = str(
                    self.last_payment_lookup_diagnostic.get("message") or ""
                ).strip()
                if diagnostic_message:
                    pending_result["msg"] = diagnostic_message
                return pending_result
            pay_url = pay_url.strip()
            return {
                "success": True,
                "pay_url": pay_url,
                "pay_type": pay_type,
                "order_id": new_order_id,
            }
        except (BuffAuthExpired, BuffRequestBlocked, BuffWriteResultUnknown):
            raise
        except Exception as e:
            return {"success": False, "code": "FAIL", "msg": str(e)}

    def _fetch_created_order_payment_url(
        self, game: str, order_id: str
    ) -> Optional[str]:
        """Fetch a newly-created order's payment URL with bounded read retries.

        BUFF can acknowledge a buy before its payment QR code is materialised.
        These are GET-only retries: they never create or alter another order.
        免密（自动扣款）订单不会产出链接（实测响应只有 auto_pay /
        buff_pay_channel）；识别到该签名时立即放弃剩余重试，由调用方转入
        免密付款状态确认。
        """

        lookup = (
            self._get_wechat_pay_url
            if self.pay_method == PAY_METHOD_WECHAT
            else self._get_alipay_url
        )
        payment_kind = "wechat" if self.pay_method == PAY_METHOD_WECHAT else "alipay"
        # The first short delay preserves the original flow.  Later retries
        # cover the normal eventual-consistency window without turning a read
        # issue into a second non-idempotent buy request.
        delays = (0.5, 1.0, 2.0, 4.0)
        for attempt, delay in enumerate(delays, start=1):
            jittered_sleep(delay)
            self._last_payment_lookup_response = {}
            self._last_response_meta = {}
            try:
                pay_url = lookup(game, order_id)
            except Exception as exc:
                self._record_payment_lookup_diagnostic(
                    payment_kind, order_id, attempt, len(delays), exception=exc
                )
                # Preserve the existing created-pending contract: once the
                # POST has created an order, a later read-side auth/risk error
                # is not evidence that the order disappeared.  The request
                # policy still retains its circuit state; do not keep retrying
                # after an explicit exception.
                return None
            if isinstance(pay_url, str) and pay_url.strip():
                if attempt > 1:
                    logger.info(
                        "BUFF 订单 %s 的 %s 支付链接在第 %d 次查询取得",
                        order_id,
                        payment_kind,
                        attempt,
                    )
                return pay_url.strip()
            self._record_payment_lookup_diagnostic(
                payment_kind, order_id, attempt, len(delays)
            )
            if _is_passwordless_cashier_response(self._last_payment_lookup_response):
                # 免密订单不会有支付链接：跳过剩余重试（每单省 3 次请求），
                # 立即返回，由调用方转入免密付款状态确认。
                return None
        return None

    def _record_payment_lookup_diagnostic(
        self,
        payment_kind: str,
        order_id: str,
        attempt: int,
        attempts: int,
        *,
        response: Optional[dict] = None,
        exception: Optional[Exception] = None,
    ) -> None:
        """Record a sanitised payment-lookup outcome for troubleshooting."""

        result = (
            response
            if isinstance(response, dict)
            else self._last_payment_lookup_response
            if isinstance(self._last_payment_lookup_response, dict)
            else {}
        )
        # The lookup helpers stash their safe response summary before calling
        # this helper.  Never retain response values: payment URLs and other
        # checkout details must not leak into logs or status objects.
        meta = dict(self._last_response_meta)
        data = result.get("data")
        message = result.get("error") or result.get("msg") or result.get("message")
        passwordless = _is_passwordless_cashier_response(result)
        diagnostic = {
            "payment_kind": payment_kind,
            "order_id": str(order_id),
            "attempt": int(attempt),
            "attempts": int(attempts),
            "http_status": meta.get("status_code"),
            "elapsed_ms": meta.get("elapsed_ms"),
            "code": str(result.get("code") or ""),
            "data_type": type(data).__name__,
            "data_keys": sorted(str(key) for key in data.keys())[:20]
            if isinstance(data, dict)
            else [],
            "error_type": type(exception).__name__ if exception else "",
            "message": str(exception or message or "")[:300],
        }
        if passwordless:
            # 只记录 auto_pay 的子键名用于确认签名，绝不保留任何渠道值。
            auto_pay = data.get("auto_pay")
            diagnostic["auto_pay_keys"] = (
                sorted(str(key) for key in auto_pay.keys())[:20]
                if isinstance(auto_pay, dict)
                else []
            )
        self.last_payment_lookup_diagnostic = diagnostic
        if passwordless:
            logger.info(
                "BUFF 订单 %s 为免密自动扣款订单（无收银台链接）kind=%s attempt=%d/%d "
                "http=%s code=%s data_keys=%s auto_pay_keys=%s",
                diagnostic["order_id"],
                diagnostic["payment_kind"],
                diagnostic["attempt"],
                diagnostic["attempts"],
                diagnostic["http_status"],
                diagnostic["code"] or "-",
                ",".join(diagnostic["data_keys"]) or "-",
                ",".join(diagnostic["auto_pay_keys"]) or "-",
            )
        else:
            logger.warning(
                "BUFF 支付链接查询未成功 order_id=%s kind=%s attempt=%d/%d "
                "http=%s code=%s data_type=%s data_keys=%s error=%s",
                diagnostic["order_id"],
                diagnostic["payment_kind"],
                diagnostic["attempt"],
                diagnostic["attempts"],
                diagnostic["http_status"],
                diagnostic["code"] or "-",
                diagnostic["data_type"],
                ",".join(diagnostic["data_keys"]) or "-",
                diagnostic["message"] or "-",
            )

    def _get_alipay_url(self, game: str, order_id: str) -> Optional[str]:
        params = {"bill_order_id": str(order_id), "_": str(int(time.time() * 1000))}
        h = {
            "Accept": "application/json, text/javascript, */*; q=0.01", 
            "X-Requested-With": "XMLHttpRequest", 
            "Referer": f"https://buff.163.com/market/buy_order/history?game={game}"
        }
        try:
            res = self._make_request("GET", API_PAGE_PAY, params=params, headers=h)
            self._last_payment_lookup_response = res if isinstance(res, dict) else {}
            if res.get("code") == "OK":
                data = res.get("data", {})
                return data.get("elements_v2", {}).get("alipay", {}).get("url") or data.get("elements", {}).get("url") or data.get("url")
        except (BuffAuthExpired, BuffRequestBlocked):
            raise
        except Exception:
            pass
        return None
    def _get_wechat_pay_url(self, game: str, order_id: str) -> Optional[str]:
        params = {"bill_order_id": str(order_id), "_": str(int(time.time() * 1000))}
        h = {"Referer": f"https://buff.163.com/market/buy_order/history?game={game}"}
        try:
            res = self._make_request("GET", API_WX_PAY_QRCODE, params=params, headers=h)
            self._last_payment_lookup_response = res if isinstance(res, dict) else {}
            if res.get("code") == "OK":
                data = res.get("data", {})
                return data.get("url") or data.get("elements_v2", {}).get("wechatpay", {}).get("url")
        except (BuffAuthExpired, BuffRequestBlocked):
            raise
        except Exception:
            pass
        return None
    def batch_buy_create(
        self,
        goods_id: int,
        max_price: float,
        num: int,
        game: str = "csgo",
    ) -> Optional[str]:
        if self.pay_method != PAY_METHOD_WECHAT:
            return None
        import uuid
        trace_id = uuid.uuid4().hex
        currency_unit = Decimal("0.01")
        exact_unit_price = Decimal(str(max_price)).quantize(
            currency_unit,
            rounding=ROUND_HALF_UP,
        )
        exact_frozen_amount = (exact_unit_price * int(num)).quantize(
            currency_unit,
            rounding=ROUND_HALF_UP,
        )
        payload = {
            "game": game,
            "goods_id": int(goods_id),
            "pay_method": PAY_METHOD_WECHAT,
            # Build the amount from decimal currency values.  A binary-float
            # product such as 0.29 * 3 becomes 0.8699999999999999, which can
            # leave the paid batch microscopically short for its final item.
            "frozen_amount": float(exact_frozen_amount),
            "max_price": format(exact_unit_price, "f"),
            "num": str(num),
            "steamid": None,
        }
        h = {
            "Referer": f"https://buff.163.com/goods/{goods_id}",
            "Buff-Cashier-Trace-Id": trace_id,
        }
        try:
            res = self._make_request("POST", API_BATCH_BUY_CREATE, headers=h, data=json.dumps(payload))
            if res.get("code") != "OK":
                return None
            data = res.get("data")
            if not isinstance(data, dict):
                raise BuffWriteResultUnknown(
                    "BUFF 批量锁单返回成功但 data 格式异常，结果未知",
                    method="POST",
                    url=API_BATCH_BUY_CREATE,
                )
            batch_id = (
                _normalize_server_identifier(data.get("id"))
                or _normalize_server_identifier(data.get("batch_buy_id"))
            )
            if not batch_id:
                raise BuffWriteResultUnknown(
                    "BUFF 批量锁单返回成功但缺少批次号，结果未知",
                    method="POST",
                    url=API_BATCH_BUY_CREATE,
                )
            return batch_id
        except (BuffAuthExpired, BuffRequestBlocked, BuffWriteResultUnknown):
            raise
        except Exception:
            return None
    def batch_buy_wx_qrcode(self, batch_id: str, game: str = "csgo") -> Optional[str]:
        params = {
            "batch_buy_id": str(batch_id),
            "_": str(int(time.time() * 1000)),
        }
        h = {"Referer": "https://buff.163.com/goods/0?from=market"}
        try:
            res = self._make_request("GET", API_BATCH_WX_PAY_QRCODE, params=params, headers=h)
            if res.get("code") != "OK":
                return None
            data = res.get("data", {})
            return data.get("url") or data.get("qrcode") or None
        except (BuffAuthExpired, BuffRequestBlocked, BuffWriteResultUnknown):
            raise
        except Exception:
            return None
    def batch_buy_finalize(
        self,
        game: str,
        goods_id: int,
        sell_order_id: str,
        price: str,
        batch_buy_id: str,
    ) -> Optional[str]:
        if self.pay_method != PAY_METHOD_WECHAT:
            return None
        import uuid
        trace_id = uuid.uuid4().hex
        payload = {
            "game": game,
            "goods_id": int(goods_id),
            "sell_order_id": str(sell_order_id),
            "price": str(price),
            "pay_method": PAY_METHOD_WECHAT,
            "batch": 1,
            "batch_buy_id": str(batch_buy_id),
            "batch_id": "",
            "allow_tradable_cooldown": 0,
            "hide_non_epay": False,
            "steamid": None,
        }
        h = {
            "Referer": f"https://buff.163.com/goods/{goods_id}",
            "Buff-Cashier-Trace-Id": trace_id
        }
        try:
            res = self._make_request("POST", API_BUY, headers=h, data=json.dumps(payload))
            if res.get("code") != "OK":
                return None
            data = res.get("data")
            if not isinstance(data, dict):
                raise BuffWriteResultUnknown(
                    "BUFF 批量核销返回成功但 data 格式异常，结果未知",
                    method="POST",
                    url=API_BUY,
                )
            order_id = _normalize_server_identifier(data.get("id"))
            if not order_id:
                raise BuffWriteResultUnknown(
                    "BUFF 批量核销返回成功但缺少订单号，结果未知",
                    method="POST",
                    url=API_BUY,
                )
            return order_id
        except (BuffAuthExpired, BuffRequestBlocked, BuffWriteResultUnknown):
            raise
        except Exception:
            return None
    def ask_seller_to_send(self, bill_order_id_or_ids, game: str = "csgo") -> bool:
        if isinstance(bill_order_id_or_ids, (list, tuple)):
            raw_ids = bill_order_id_or_ids
        else:
            raw_ids = (
                [bill_order_id_or_ids]
                if bill_order_id_or_ids is not None
                else []
            )

        # One BUFF bill may only be prompted once in this pass.  Normalize and
        # de-duplicate without changing order so a duplicate local row cannot
        # consume a request slot while another bill is skipped.
        ids = []
        seen_ids = set()
        for raw_id in raw_ids:
            order_id = _normalize_server_identifier(raw_id)
            if not order_id or order_id in seen_ids:
                continue
            seen_ids.add(order_id)
            ids.append(order_id)
        if not ids:
            return False

        h = {"Referer": f"https://buff.163.com/market/buy_order/history?game={game}"}
        succeeded_ids = []
        failed_ids = []
        requests_sent = 0

        for order_id in ids:
            payload = {
                "bill_orders": [order_id],
                "game": game,
                "steamid": None,
            }
            prompted = False
            last_error = ""

            for attempt in range(ASK_SELLER_MAX_ATTEMPTS):
                if requests_sent > 0:
                    delay = ASK_SELLER_BETWEEN_REQUEST_SECONDS
                    if attempt > 0:
                        delay *= attempt + 1
                    jittered_sleep(delay)
                requests_sent += 1

                try:
                    res = self._make_request(
                        "POST",
                        API_ASK_SELLER_SEND,
                        headers=h,
                        data=json.dumps(payload),
                    )
                except (
                    BuffAuthExpired,
                    BuffRequestBlocked,
                    BuffWriteResultUnknown,
                ):
                    # A transport/risk/auth failure after a write was sent has
                    # an unknown result.  Retrying it automatically could
                    # amplify risk-control traffic, so preserve the hard stop.
                    raise
                except Exception as exc:
                    raise BuffWriteResultUnknown(
                        "提醒卖家发货时发生异常，写请求结果未知，禁止自动重试",
                        method="POST",
                        url=API_ASK_SELLER_SEND,
                    ) from exc

                if res.get("code") == "OK":
                    prompted = True
                    succeeded_ids.append(order_id)
                    break

                last_error = str(
                    res.get("error")
                    or res.get("msg")
                    or res.get("message")
                    or res.get("code")
                    or "未知错误"
                )
                logger.warning(
                    "提醒卖家发货失败 bill_order_id=%s attempt=%s/%s: %s",
                    order_id,
                    attempt + 1,
                    ASK_SELLER_MAX_ATTEMPTS,
                    last_error,
                )

            if not prompted:
                failed_ids.append(order_id)

        if failed_ids:
            logger.warning(
                "提醒卖家发货仅完成 %s/%s，失败订单: %s",
                len(succeeded_ids),
                len(ids),
                ", ".join(failed_ids),
            )
            return False

        logger.info("提醒卖家发货全部完成 %s/%s", len(succeeded_ids), len(ids))
        return len(succeeded_ids) == len(ids)

    def buyer_send_offer(self, steam_cookies: str, bill_orders) -> dict:
        """买家侧发起 Steam 报价（手动+ 订单，BUFF 服务端代发）。

        与官方 APP「一键发送」同一机制：把加密后的 Steam Cookie 提交给
        BUFF，由 BUFF 用该 Steam 会话向卖家发起交易报价；报价的卖家与物品
        均由 BUFF 订单数据决定，调用方只提供自己的 Steam 会话。只接受显式
        传入的 Cookie，适配器不读取应用配置。

        返回 ``{"success": bool, "data": dict, "error": str}``；写请求结果
        未知时向上抛出，调用方不得自动重试。
        """

        if not str(steam_cookies or "").strip():
            return {"success": False, "data": {}, "error": "缺少 Steam 凭证"}
        ids = []
        seen_ids = set()
        for raw_id in bill_orders if isinstance(bill_orders, (list, tuple)) else [bill_orders]:
            order_id = _normalize_server_identifier(raw_id)
            if order_id and order_id not in seen_ids:
                seen_ids.add(order_id)
                ids.append(order_id)
        if not ids:
            return {"success": False, "data": {}, "error": "缺少有效的买单号"}

        payload = {
            "buyer_info": encrypt_buff_payload(str(steam_cookies).strip()),
            "bill_orders": ids,
        }
        h = {"Referer": "https://buff.163.com/market/buy_order/history"}
        res = self._make_request(
            "POST",
            API_MANUAL_PLUS_BUYER_SEND_OFFER,
            headers=h,
            data=json.dumps(payload),
        )
        if not isinstance(res, dict) or res.get("code") != "OK":
            error = "BUFF 返回异常"
            if isinstance(res, dict):
                error = str(
                    res.get("error")
                    or res.get("msg")
                    or res.get("message")
                    or res.get("code")
                    or error
                )
            data = res.get("data") if isinstance(res, dict) else None
            return {
                "success": False,
                "data": data if isinstance(data, dict) else {},
                "error": error,
            }
        data = res.get("data")
        return {
            "success": True,
            "data": data if isinstance(data, dict) else {},
            "error": "",
        }
