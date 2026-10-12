"""Purchase market responsibility."""

from __future__ import annotations
import re
import statistics
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional
from app.services.steam_client import SteamClient
from app.services.buff_client import count_lowest_price_orders
from app.purchase.models import (
    BUFF_ORDERS_CACHE_TTL_SECONDS,
)


def _cache_buff_sell_orders(item: Dict[str, Any], orders: list) -> None:
    """Cache one coherent BUFF snapshot and record when it was fetched."""
    item["_buff_sell_orders"] = orders
    item["_buff_sell_orders_fetched_at"] = time.time()


def _buff_orders_cache_is_fresh(
    item: Dict[str, Any],
    ttl_seconds: float = BUFF_ORDERS_CACHE_TTL_SECONDS,
) -> bool:
    orders = item.get("_buff_sell_orders")
    fetched_at = item.get("_buff_sell_orders_fetched_at")
    if not orders or not isinstance(fetched_at, (int, float)):
        return False
    age = time.time() - float(fetched_at)
    return 0 <= age <= max(0.0, min(float(ttl_seconds), BUFF_ORDERS_CACHE_TTL_SECONDS))


def _fetch_steam_sell_data(
    market_hash_name: str,
    config: dict,
    app_id: int = 730,
    *,
    return_error: bool = False,
):
    from app.configuration.repository import get_steam_credentials
    from steam.session import create_market_session
    from steam.market_orders import get_sell_orders_cny, compute_smart_list_price

    name = (market_hash_name or "").strip()
    if not name:
        reason = "Steam 市场名为空"
        return (None, reason) if return_error else None
    cred = get_steam_credentials()
    cookies = cred.get("cookies", "")
    steam_id = cred.get("steam_id", "")
    if not cookies or not steam_id:
        missing = []
        if not cookies:
            missing.append("Cookie")
        if not steam_id:
            missing.append("steam_id")
        reason = "Steam 凭据缺失: " + "、".join(missing)
        return (None, reason) if return_error else None
    try:
        session = create_market_session(cookies, steam_id)
        cfg = config.get("pipeline", {})
        wall_volume = int(cfg.get("sell_price_wall_volume", 20))
        max_ignore = int(cfg.get("sell_price_max_ignore_volume", 4))
        usd_to_cny_rate = float(cfg.get("usd_to_cny", 7.2))
        orders_result = get_sell_orders_cny(
            session,
            name,
            app_id=app_id,
            request_delay=1.0,
            return_error=True,
            usd_to_cny_rate=usd_to_cny_rate,
        )
        if isinstance(orders_result, tuple) and len(orders_result) == 2:
            result, reason = orders_result
        else:
            result, reason = orders_result, None
        if not result:
            return (None, reason or "Steam 卖单接口返回空数据") if return_error else None
        if not result.get("sell_orders"):
            return (None, reason or "Steam 返回空卖单图") if return_error else None
        orders = result["sell_orders"]
        price, _ = compute_smart_list_price(
            orders,
            wall_volume_threshold=wall_volume,
            max_ignore_volume=max_ignore,
            min_step=0,
            offset=0,
        )
        if price is None:
            reason = "Steam 卖单已获取，但无法计算智能参考价"
            return (None, reason) if return_error else None
        data = {"sell_orders": orders, "smart_price": price}
        return (data, None) if return_error else data
    except Exception as e:
        detail = str(e).strip()
        if len(detail) > 120:
            detail = detail[:117] + "..."
        reason = f"Steam 卖单获取异常: {type(e).__name__}" + (f" - {detail}" if detail else "")
        return (None, reason) if return_error else None


def _check_buff_price(
    item,
    gid,
    plan_price,
    buff_client,
    config: dict,
    log_fn,
):
    # 拉取 Buff 实时最低价，和 iflow 价格对比确认没有跳动
    # 成功返回 (True, 最新价格)，失败返回 (False, None)
    # 注意：BuffAuthExpired 要直接往上抛，不能在这里吃掉
    buff_cfg = config.get("buff", {})
    game_buff = buff_cfg.get("game", "csgo")
    tolerance = float(buff_cfg.get("price_tolerance", 0.5))
    orders = buff_client.get_sell_orders(gid, game_buff)
    if not orders:
        if log_fn:
            reason = (
                "接口返回 None，可能是网络/鉴权/风控问题"
                if orders is None
                else "Buff 当前无在售卖单"
            )
            log_fn(
                f"[Buff]   → 预检未通过: 无法获取 Buff 卖单信息：{reason} (goods_id={gid})", "warn"
            )
        return False, None
    lowest_price, _ = count_lowest_price_orders(orders)
    if lowest_price <= 0:
        if log_fn:
            log_fn(f"[Buff]   → 预检未通过: Buff 最低价无效 (goods_id={gid})", "warn")
        return False, None
    if plan_price is not None and lowest_price - plan_price > tolerance:
        if log_fn:
            log_fn(
                f"[Buff]   → 预检未通过: Buff 最低价 {lowest_price:.2f} 较 iflow 参考价 {plan_price:.2f} 超出容忍 (差{lowest_price - plan_price:.2f})",
                "warn",
            )
        return False, None
    item["_buff_lowest_price"] = lowest_price
    _cache_buff_sell_orders(item, orders)
    return True, lowest_price


def _adjust_ref_price_for_daily_high(
    market_hash_name: str,
    current_ref_price: float,
    config: dict,
    log_fn: Optional[Callable[[str, str], None]],
    app_id: int = 730,
) -> float:
    from utils.time import parse_steam_history_date
    from utils.money import apply_currency, USD_TO_CNY_DEFAULT

    name = (market_hash_name or "").strip()
    if not name or current_ref_price <= 0:
        return current_ref_price
    steam_client = SteamClient()
    raw = steam_client.fetch_history(name, app_id=app_id, return_currency=True)
    if not raw:
        return current_ref_price
    history = raw.get("history") if isinstance(raw, dict) else raw
    if not isinstance(history, list) or not history:
        return current_ref_price
    currency = raw.get("currency") if isinstance(raw, dict) else None
    usd_cny = float(config.get("pipeline", {}).get("usd_to_cny", USD_TO_CNY_DEFAULT))
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    prices_cny: List[float] = []
    for item in history:
        if len(item) < 2:
            continue
        dt = parse_steam_history_date(str(item[0]))
        if dt is None or dt < cutoff:
            continue
        try:
            p = float(item[1])
            converted, _ = apply_currency([p], currency, usd_cny)
            prices_cny.append(converted[0])
        except (ValueError, TypeError):
            continue
    if len(prices_cny) < 2:
        return current_ref_price
    sorted_prices = sorted(prices_cny)
    trimmed_prices = sorted_prices[1:-1] if len(sorted_prices) >= 3 else sorted_prices
    if not trimmed_prices:
        return current_ref_price
    trimmed_low = trimmed_prices[0]
    trimmed_high = trimmed_prices[-1]
    daily_avg = statistics.mean(trimmed_prices)
    if trimmed_high <= trimmed_low:
        return current_ref_price
    daily_position = (current_ref_price - trimmed_low) / (trimmed_high - trimmed_low)
    if daily_position <= 0.6:
        return current_ref_price
    candidate_price = (current_ref_price + daily_avg) / 2
    conservative_price = min(current_ref_price, candidate_price)
    if log_fn:
        log_fn(
            f"[Buff]   → 检测到 Steam 价格处于日内高位 (位置: {daily_position:.2f})，存在虚高风险",
            "warn",
        )
        if conservative_price < current_ref_price:
            log_fn(
                f"[Buff]   → 降级参考价: {current_ref_price:.2f} -> {conservative_price:.2f} (当前价与去极值均价折中)",
                "info",
            )
        else:
            log_fn(
                f"[Buff]   → 降级参考价保持: {current_ref_price:.2f} (去极值均价不低于当前价，避免降级抬价)",
                "info",
            )
    return conservative_price


def _compute_sell_pressure_from_orders(
    sell_orders: list,
    daily_volume: int,
    n_orders: int = 5,
) -> Optional[float]:
    if daily_volume <= 0 or not sell_orders:
        return None
    target_orders = sorted(sell_orders, key=lambda x: x[0])[:n_orders]
    total_vol = sum(vol for _, vol in target_orders)
    base_pressure = total_vol / daily_volume
    if len(target_orders) < 2:
        return base_pressure
    current_vol = 0
    wall_vol = 0
    for i in range(len(target_orders) - 1):
        p_curr, c_curr = target_orders[i]
        p_next, _ = target_orders[i + 1]
        current_vol += c_curr
        if p_curr < 5.0:
            gap_abs, gap_rel = 0.10, 0.08
        elif p_curr < 20.0:
            gap_abs, gap_rel = 0.30, 0.05
        elif p_curr < 100.0:
            gap_abs, gap_rel = 1.0, 0.03
        elif p_curr < 500.0:
            gap_abs, gap_rel = 5.0, 0.02
        else:
            gap_abs, gap_rel = 10.0, 0.015
        threshold = max(gap_abs, p_curr * gap_rel)
        if (p_next - p_curr) > threshold:
            wall_vol = current_vol
            break
    if wall_vol > 0 and (wall_vol <= max(3, daily_volume * 0.15)):
        return base_pressure * 0.4
    return base_pressure


def _fetch_smart_market_price(
    market_hash_name: str, config: dict, app_id: int = 730
) -> Optional[float]:
    data = _fetch_steam_sell_data(market_hash_name, config, app_id)
    return data.get("smart_price") if data else None


def _parse_threshold(val) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _goods_id_from_buff_url(url: str) -> int:
    if not url or "buff.163.com" not in url:
        return 0
    m = re.search(r"/goods/(\d+)", url)
    return int(m.group(1)) if m else 0
