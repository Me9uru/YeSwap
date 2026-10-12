"""Steam 社区/市场 HTTP 会话工厂。

会话统一使用 curl_cffi 的 Chrome 指纹伪装（TLS/HTTP2 与请求头与真实浏览器一致）。
Steam 风控会按客户端指纹拒绝请求：用 requests/urllib3 携带写死的浏览器 UA
（TLS 指纹仍是 Python 的）会被持续 429，因此访问 steamcommunity.com 的代码必须
复用本模块的工厂，不要手写 User-Agent / Accept-Encoding（否则指纹与头不一致，
反而更容易被风控）。
"""
from typing import Optional

from curl_cffi import requests as curl_requests

from utils.cookies import parse_cookies as parse_cookies

IMPERSONATE = "chrome"
LOGIN_PROBE_URL = "https://steamcommunity.com/my/"


def _session_proxies() -> Optional[dict]:
    """按代理池策略为 Steam 社区会话选择代理。

    策略2 始终走代理；策略3/未启用 直连；策略1 先直连，请求失败后由调用方通过
    `get_proxies_for_request(failed=True)` 取代理重试。steamcommunity.com 在部分
    网络下无法直连，会话必须带上代理，否则库存/市场请求只会超时。
    """
    from utils.proxy_manager import get_proxy_manager
    return get_proxy_manager().get_proxies_for_request(failed=False)


def build_steam_session(
    *,
    cookies=None,
    headers: Optional[dict] = None,
    verify: bool = False,
    proxies: Optional[dict] = None,
) -> curl_requests.Session:
    """构建带浏览器指纹的 Steam 会话。

    ``proxies=None`` 时按代理池策略解析；传空 dict 表示明确不走代理。
    ``headers`` 只能补充与 Steam Web UI 一致的请求头（如 X-Requested-With、
    Referer），传入 User-Agent/Accept-Encoding 会破坏指纹一致性。
    """
    session = curl_requests.Session(impersonate=IMPERSONATE, verify=verify)
    if cookies:
        session.cookies.update(parse_cookies(cookies))
    if headers:
        session.headers.update(headers)
    resolved = _session_proxies() if proxies is None else proxies
    if resolved:
        session.proxies.update(resolved)
    return session


def create_market_session(
    cookies_raw,
    steam_id: str,
    *,
    headers: Optional[dict] = None,
    verify: bool = False,
    proxies: Optional[dict] = None,
) -> curl_requests.Session:
    h = {
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"https://steamcommunity.com/profiles/{steam_id}/inventory/",
    }
    if headers:
        h.update(headers)
    return build_steam_session(cookies=cookies_raw, headers=h, verify=verify, proxies=proxies)


def check_login_state(session) -> Optional[bool]:
    """主动探测会话登录态：True 已登录 / False 未登录 / None 未知。

    ``/my/`` 登录后重定向到个人资料页；Cookie 失效则跳转登录页。网络异常等
    无法判定的情况返回 None，调用方不得据此认定登录已过期。
    """
    try:
        r = session.get(LOGIN_PROBE_URL, timeout=15, allow_redirects=True)
    except Exception:
        return None
    status = getattr(r, "status_code", None)
    if status in (401, 403):
        return False
    if status != 200:
        return None
    final_url = str(getattr(r, "url", "") or "")
    if "/login" in final_url or "login.steampowered.com" in final_url:
        return False
    if "/profiles/" in final_url or "/id/" in final_url:
        return True
    return None
