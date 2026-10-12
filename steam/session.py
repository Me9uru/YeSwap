from typing import Optional
import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
MARKET_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.5845.97 Safari/537.36",
    "X-Requested-With": "XMLHttpRequest",
}
from utils.cookies import parse_cookies as parse_cookies
def _session_proxies() -> Optional[dict]:
    """按代理池策略为 Steam 社区会话选择代理。

    策略2 始终走代理；策略3/未启用 直连；策略1 先直连，请求失败后由调用方通过
    `get_proxies_for_request(failed=True)` 取代理重试。steamcommunity.com 在部分
    网络下无法直连，会话必须带上代理，否则库存/市场请求只会超时。
    """
    from utils.proxy_manager import get_proxy_manager
    return get_proxy_manager().get_proxies_for_request(failed=False)
def create_market_session(
    cookies_raw: str,
    steam_id: str,
    *,
    headers: Optional[dict] = None,
    verify: bool = False,
    proxies: Optional[dict] = None,
) -> requests.Session:
    session = requests.Session()
    session.verify = verify
    session.cookies.update(parse_cookies(cookies_raw))
    h = {**MARKET_HEADERS, "Referer": f"https://steamcommunity.com/profiles/{steam_id}/inventory/"}
    if headers:
        h.update(headers)
    session.headers.update(h)
    resolved = _session_proxies() if proxies is None else proxies
    if resolved:
        session.proxies.update(resolved)
    return session
