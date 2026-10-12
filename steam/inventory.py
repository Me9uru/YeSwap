import logging
import time
from typing import List, Optional, Tuple
class SteamAuthExpired(Exception):
    pass
INVENTORY_APP_ID = 753
INVENTORY_CONTEXT_ID = 6
CS2_APP_ID = 730
CS2_CONTEXT_MAIN = 2
CS2_CONTEXT_SECONDARY = 16
_logger = logging.getLogger(__name__)
_warn_last_at: dict = {}
_WARN_INTERVAL_SECONDS = 60.0
def _warn_once(key: str, msg: str, *args) -> None:
    """同一原因的告警在冷却期内只输出一次，避免后台轮询刷屏。"""
    now = time.monotonic()
    if now - _warn_last_at.get(key, 0.0) < _WARN_INTERVAL_SECONDS:
        return
    _warn_last_at[key] = now
    _logger.warning(msg, *args)
def _proxy_retry_proxies(session) -> Optional[dict]:
    """会话本身未走代理时取一个代理用于失败重试；已在走代理则不叠加。"""
    if getattr(session, "proxies", None):
        return None
    from utils.proxy_manager import get_proxy_manager
    return get_proxy_manager().get_proxies_for_request(failed=True)
def _request_page(session, url: str, params: dict):
    """请求库存分页；直连失败时按代理池策略改用代理重试一次（策略1：本机优先）。"""
    try:
        return session.get(url, params=params, timeout=15)
    except Exception:
        proxies = _proxy_retry_proxies(session)
        if not proxies:
            raise
        return session.get(url, params=params, timeout=15, proxies=proxies)
def fetch_inventory(
    session,
    steam_id: str,
    app_id: int = INVENTORY_APP_ID,
    context_id: int = INVENTORY_CONTEXT_ID,
    *,
    count: int = 75,
    lang: str = "english",
) -> Optional[dict]:
    url = f"https://steamcommunity.com/inventory/{steam_id}/{app_id}/{context_id}"
    try:
        r = session.get(
            url,
            params={"l": lang, "count": count},
            timeout=15,
        )
        if r.status_code != 200:
            return None
        return r.json()
    except Exception:
        return None
def check_session_validity(data: dict) -> Tuple[bool, str]:
    if not data or data.get("success") != 1:
        return False, "API 请求失败 (success != 1)"
    assets = data.get("assets")
    total_count_exists = "total_inventory_count" in data
    if assets is None and not total_count_exists:
        return False, "Cookie 已失效 (assets=None 且无 total_inventory_count)"
    return True, "Session 有效"
def _fetch_full_context(
    session,
    steam_id: str,
    app_id: int,
    context_id: int,
    *,
    count: int = 75,
    lang: str = "english",
) -> Optional[Tuple[List[dict], List[dict]]]:
    from utils.delay import jittered_sleep
    url = f"https://steamcommunity.com/inventory/{steam_id}/{app_id}/{context_id}"
    all_assets: List[dict] = []
    descriptions_map: dict = {}
    last_assetid: Optional[str] = None
    _429_attempts = 0
    _MAX_429_RETRIES = 3
    while True:
        params = {
            "l": lang,
            "count": str(count),
            "preserve_bbcode": "1",
            "raw_asset_properties": "1",
        }
        if last_assetid:
            params["start_assetid"] = last_assetid
        try:
            r = _request_page(session, url, params)
            if r.status_code == 403:
                raise SteamAuthExpired()
            if r.status_code != 200:
                if r.status_code == 429:
                    _429_attempts += 1
                    if _429_attempts > _MAX_429_RETRIES:
                        _warn_once(
                            "429",
                            "Steam 库存接口持续返回 429（限流），放弃本次拉取 appid=%s contextid=%s",
                            app_id,
                            context_id,
                        )
                        return None
                    jittered_sleep(10 * (2 ** (_429_attempts - 1)))
                    continue
                _warn_once(
                    f"http:{r.status_code}",
                    "Steam 库存接口返回 HTTP %s，放弃本次拉取 appid=%s contextid=%s",
                    r.status_code,
                    app_id,
                    context_id,
                )
                return None
            try:
                data = r.json()
            except Exception:
                if r.text and "login" in r.text.lower()[:8000]:
                    raise SteamAuthExpired()
                _warn_once(
                    "non-json",
                    "Steam 库存接口返回非 JSON 内容，放弃本次拉取 appid=%s contextid=%s",
                    app_id,
                    context_id,
                )
                return None
            valid, msg = check_session_validity(data)
            if not valid:
                raise SteamAuthExpired()
        except SteamAuthExpired:
            raise
        except Exception as e:
            _warn_once(
                f"exc:{type(e).__name__}",
                "Steam 库存请求异常 %s: %s（请检查本机到 steamcommunity.com 的直连与代理池配置）",
                type(e).__name__,
                e,
            )
            return None
        current_assets = data.get("assets") or []
        current_descs = data.get("descriptions") or []
        if not current_assets:
            break
        all_assets.extend(current_assets)
        for d in current_descs:
            cid = d.get("classid")
            if cid:
                iid = d.get("instanceid", "0")
                descriptions_map[(str(cid), str(iid))] = d
        more_items = data.get("more_items", 0) == 1
        if not more_items:
            break
        last_assetid = data.get("last_assetid")
        if not last_assetid and current_assets:
            last_assetid = current_assets[-1].get("assetid")
        if not last_assetid:
            break
        jittered_sleep(1)
    return all_assets, list(descriptions_map.values())
def fetch_cs2_inventory(
    session,
    steam_id: str,
    *,
    lang: str = "english",
    count: int = 75,
) -> Optional[dict]:
    from concurrent.futures import ThreadPoolExecutor
    combined_assets: List[dict] = []
    combined_descs: List[dict] = []
    def _fetch_ctx(context_id: int):
        return _fetch_full_context(session, steam_id, CS2_APP_ID, context_id, count=count, lang=lang)
    results = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        future_main = pool.submit(_fetch_ctx, CS2_CONTEXT_MAIN)
        future_sec = pool.submit(_fetch_ctx, CS2_CONTEXT_SECONDARY)
        for ctx_name, fut in [("main", future_main), ("secondary", future_sec)]:
            try:
                results[ctx_name] = fut.result()
            except SteamAuthExpired:
                return {"auth_expired": True}
            except Exception:
                results[ctx_name] = None
    succeeded = False
    for key in ("main", "secondary"):
        ctx_data = results.get(key)
        if ctx_data is None:
            continue
        succeeded = True
        a, d = ctx_data
        combined_assets.extend(a)
        combined_descs.extend(d)
    if not succeeded:
        # 只有全部上下文都失败才算拉取失败；成功但为空的库存是正常结果，
        # 否则空库存会被误报成“获取 CS2 库存失败”。
        return None
    unique_desc = {}
    for d in combined_descs:
        cid = d.get("classid")
        if cid:
            iid = d.get("instanceid", "0")
            unique_desc[(str(cid), str(iid))] = d
    return {
        "assets": combined_assets,
        "descriptions": list(unique_desc.values()),
        "total_count": len(combined_assets),
    }
def find_asset_by_name(data: Optional[dict], item_name: str) -> Optional[dict]:
    if not data:
        return None
    descriptions = data.get("descriptions") or []
    assets_list = data.get("assets") or []
    class_id = next((d["classid"] for d in descriptions if d.get("name") == item_name), None)
    if not class_id:
        return None
    asset = next((a for a in assets_list if a.get("classid") == class_id), None)
    if not asset:
        return None
    return {
        "assetid": asset["assetid"],
        "appid": asset["appid"],
        "contextid": asset["contextid"],
        "classid": asset["classid"],
    }
