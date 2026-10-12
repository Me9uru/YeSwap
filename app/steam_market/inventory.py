import re
import threading
import time
"""CS2 库存读取与冷却期解析。"""
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from config import get_steam
from steam.inventory import CS2_APP_ID, SteamRateLimited, fetch_cs2_inventory
from steam.session import check_login_state, create_market_session

# 库存接口 429 冷却：跨调用方共享（路由、出售流程、后台任务都经 scan_cs2_inventory），
# 模式与 app/services/steam_client.py 的历史价格冷却一致。
_inventory_rate_limit_lock = threading.Lock()
_inventory_rate_limit_until = 0.0
_inventory_rate_limit_streak = 0
_inventory_rate_limit_base_seconds = 30.0
_inventory_rate_limit_max_seconds = 300.0


def _inventory_cooldown_remaining() -> float:
    with _inventory_rate_limit_lock:
        return max(0.0, _inventory_rate_limit_until - time.monotonic())


def _record_inventory_rate_limit(retry_after: Optional[float] = None) -> float:
    global _inventory_rate_limit_streak, _inventory_rate_limit_until
    with _inventory_rate_limit_lock:
        _inventory_rate_limit_streak += 1
        backoff = min(
            _inventory_rate_limit_max_seconds,
            _inventory_rate_limit_base_seconds * (2 ** (_inventory_rate_limit_streak - 1)),
        )
        cooldown = max(backoff, retry_after or 0.0)
        _inventory_rate_limit_until = time.monotonic() + cooldown
        return cooldown


def _record_inventory_success() -> None:
    global _inventory_rate_limit_streak, _inventory_rate_limit_until
    with _inventory_rate_limit_lock:
        _inventory_rate_limit_streak = 0
        _inventory_rate_limit_until = 0.0


def _rate_limit_message(seconds: float) -> str:
    return f"Steam 库存接口限流中，约 {int(seconds + 0.999)} 秒后可重试"
def _safe_iso(ts: float) -> Optional[str]:
    if not ts:
        return None
    try:
        return datetime.utcfromtimestamp(ts).isoformat() + "Z"
    except (OSError, OverflowError, ValueError):
        return None
def _parse_cooldown(owner_descriptions: List[dict]) -> Tuple[str, float]:
    text = ""
    ts = 0.0
    for d in owner_descriptions or []:
        val = d.get("value") or ""
        if "trade-protected" not in val:
            continue
        text = val
        m = re.search(r"until (.+?) GMT", val)
        if not m:
            break
        raw = m.group(1)
        raw = raw.replace(" (", " ").replace(")", "")
        try:
            dt = datetime.strptime(raw, "%b %d, %Y %H:%M:%S").replace(tzinfo=timezone.utc)
            ts = dt.timestamp()
        except Exception:
            ts = 0.0
        break
    return text, ts
def scan_cs2_inventory() -> Tuple[bool, List[Dict[str, Any]], str]:
    cred = get_steam()
    steam_id = cred.get("steam_id")
    cookies = cred.get("cookies")
    if not steam_id or not cookies:
        return False, [], "未配置 Steam steam_id 或 cookies"
    remaining = _inventory_cooldown_remaining()
    if remaining > 0:
        return False, [], _rate_limit_message(remaining)
    session = create_market_session(cookies, steam_id)
    try:
        data = fetch_cs2_inventory(session, steam_id)
    except SteamRateLimited as e:
        cooldown = _record_inventory_rate_limit(e.retry_after)
        return False, [], _rate_limit_message(cooldown)
    _record_inventory_success()
    if not data:
        return False, [], "获取 CS2 库存失败"
    if isinstance(data, dict) and data.get("auth_expired"):
        # ctx16 对匿名请求会返回 {"success":1,"rwgrsn":-2}，与"Cookie 失效"形态相同，
        # 需主动探测区分：确认已登录时按空库存处理，避免误触发密码重登。
        if check_login_state(session) is True:
            return True, [], ""
        return False, [], "登录已过期，请重新登录"
    items: List[Dict[str, Any]] = []
    now = time.time()
    desc_map: Dict[tuple, Dict[str, Any]] = {}
    for d in data.get("descriptions") or []:
        cid = d.get("classid")
        if cid:
            iid = d.get("instanceid") or "0"
            desc_map[(str(cid), str(iid))] = d
    for asset in data.get("assets") or []:
        cid = str(asset.get("classid", ""))
        iid = str(asset.get("instanceid") or "0")
        desc = desc_map.get((cid, iid)) or desc_map.get((cid, "0"))
        if not desc:
            continue
        name = desc.get("name") or ""
        market_hash_name = desc.get("market_hash_name") or desc.get("market_name") or name
        marketable = int(desc.get("marketable", 0))
        tradable = int(desc.get("tradable", 0))
        owner_desc = desc.get("owner_descriptions") or []
        cd_text, cd_ts = _parse_cooldown(owner_desc)
        can_trade = tradable == 1 and (not cd_ts or now >= cd_ts)
        can_sell = marketable == 1 and can_trade
        items.append(
            {
                "name": name,
                "market_hash_name": market_hash_name,
                "assetid": str(asset.get("assetid", "")),
                "appid": int(asset.get("appid", CS2_APP_ID)),
                "contextid": str(asset.get("contextid", "")),
                "marketable": marketable,
                "tradable": tradable,
                "cooldown_text": cd_text,
                "cooldown_at": cd_ts or None,
                "cooldown_at_iso": _safe_iso(cd_ts),
                "can_sell": can_sell,
                "can_trade": can_trade,
            }
        )
    if not items:
        # 空库存与"Cookie 失效导致匿名视图为空"在前一步无法区分，探测一次登录态。
        state = check_login_state(session)
        if state is False:
            return False, [], "登录已过期，请重新登录"
        if state is None:
            from app.runtime.state import log
            log("inventory: 库存为空且登录态探测失败（网络异常），按空库存处理", "warn", category="steam")
    items.sort(key=lambda x: (not x["can_sell"], not x["can_trade"], x["name"]))
    return True, items, ""
