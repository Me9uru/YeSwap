"""Purchase checkout responsibility."""

from __future__ import annotations
from app.strategies.rules import discount_is_acceptable
from typing import Any, Callable, Dict, Optional
from app.runtime.state import get_purchases
from app.strategies.runtime import is_strategy_module_enabled
from app.services.buff_client import count_lowest_price_orders
from app.purchase.models import (
    BUFF_ORDERS_CACHE_TTL_SECONDS,
    PurchaseContext,
    SKIP_VERIFICATION_FAILED,
    TARGET_REACHED,
    _PurchaseAttemptStatus,
    _affordable_quantity,
    _finish_attempt,
)
from utils.money import estimated_purchase_discount_ratio
from app.purchase.selection import (
    SKIP_NO_FAILED,
)
from app.purchase.market import (
    _adjust_ref_price_for_daily_high,
    _buff_orders_cache_is_fresh,
    _cache_buff_sell_orders,
    _compute_sell_pressure_from_orders,
    _effective_price_tolerance,
    _fetch_steam_sell_data,
    _parse_threshold,
)
from app.purchase.batch import (
    _try_batch_buy,
)
from app.purchase.single import (
    _try_single_buy,
)


def lock_and_confirm_payment(
    buff_client: Any,
    item: Dict[str, Any],
    config: dict,
    target_balance: float,
    acc: float,
    set_pending_payment: callable,
    wait_payment_confirm: callable,
    confirm_payment: callable,
    is_stop_requested: callable,
    append_purchase: callable,
    log_fn: Optional[Callable[[str, str], None]] = None,
    on_entering_payment: Optional[Callable[[], None]] = None,
    is_time_allowed: Optional[Callable[[], bool]] = None,
) -> Optional[float]:
    buff_cfg = config.get("buff", {})
    game_buff = buff_cfg.get("game", "csgo")
    goods_id = item["goods_id"]
    plan_price = item.get("_buff_lowest_price") or item.get("min_price")
    tolerance = _effective_price_tolerance(plan_price, buff_cfg)
    try:
        orders_cache_ttl = float(
            config.get("pipeline", {}).get(
                "buff_sell_orders_cache_ttl_seconds",
                BUFF_ORDERS_CACHE_TTL_SECONDS,
            )
        )
    except (TypeError, ValueError):
        orders_cache_ttl = BUFF_ORDERS_CACHE_TTL_SECONDS
    orders_cache_ttl = max(2.0, min(orders_cache_ttl, BUFF_ORDERS_CACHE_TTL_SECONDS))
    orders = (
        item.get("_buff_sell_orders")
        if _buff_orders_cache_is_fresh(item, orders_cache_ttl)
        else None
    )
    if orders is None:
        orders = buff_client.get_sell_orders(goods_id, game_buff)
        if orders:
            _cache_buff_sell_orders(item, orders)
        if log_fn:
            log_fn(
                f"[Buff] 拉取在售 goods_id={goods_id} game={game_buff} → {len(orders or [])} 条",
                "info",
            )
    else:
        if log_fn:
            log_fn(
                f"[Buff]   → 复用 {orders_cache_ttl:.1f} 秒 TTL 内的 Buff 卖单数据 → {len(orders)} 条",
                "info",
            )
    if not orders:
        if log_fn:
            reason = (
                "接口返回 None，可能是网络/鉴权/风控问题"
                if orders is None
                else "Buff 当前无在售卖单"
            )
            log_fn(f"[Buff]   → 无法获取 Buff 卖单信息：{reason}，跳过本件", "warn")
        return None
    lowest_price, count_at_lowest = count_lowest_price_orders(orders)
    if lowest_price <= 0 or count_at_lowest <= 0:
        if log_fn:
            log_fn("[Buff]   → 最低价或可购买数量无效，停止本次购买", "warn")
        return None
    if log_fn:
        log_fn(
            f"[Buff]   → 最低价={lowest_price:.2f} 同价数量={count_at_lowest} 参考价={plan_price} 容忍={tolerance} 累计={acc:.2f} 目标={target_balance}",
            "info",
        )
    if _affordable_quantity(target_balance, acc, lowest_price) < 1:
        if log_fn:
            log_fn(f"[Buff]   → 累计+本件={acc + lowest_price:.2f} 已达/超过目标，不再锁单", "info")
        return TARGET_REACHED
    if plan_price is not None and lowest_price - plan_price > tolerance:
        if log_fn:
            log_fn(
                f"[Buff]   → 当前价较参考价超出容忍 (差{lowest_price - plan_price:.2f})，跳过",
                "warn",
            )
        return None
    market_hash_name = (item.get("steam_market_name") or item.get("name") or "").strip()
    scfg = config.get("pipeline", {})
    max_discount = (
        scfg.get("max_discount")
        if is_strategy_module_enabled(config, "buy", "guard.max_discount")
        else None
    )
    sell_pressure_threshold = (
        _parse_threshold(scfg.get("sell_pressure_threshold"))
        if is_strategy_module_enabled(config, "buy", "guard.sell_pressure")
        else None
    )
    steam_depth_enabled = is_strategy_module_enabled(config, "buy", "buy.steam_sell_depth")
    need_steam = steam_depth_enabled and (
        max_discount is not None
        or (
            sell_pressure_threshold is not None
            and sell_pressure_threshold > 0
            and int(item.get("daily_volume", 0) or 0) > 0
        )
    )
    cached_steam_data = item.get("_steam_sell_data")
    steam_sell_error = None
    if need_steam:
        if cached_steam_data is not None:
            steam_sell_data = cached_steam_data
        else:
            steam_sell_data, steam_sell_error = _fetch_steam_sell_data(
                market_hash_name, config, app_id=730, return_error=True
            )
        if cached_steam_data is not None and log_fn:
            log_fn("[Buff]   → 复用稳定性阶段已缓存的 Steam 卖单数据", "info")
    else:
        steam_sell_data = None
    ref_price = steam_sell_data.get("smart_price") if steam_sell_data else None
    sell_orders = steam_sell_data.get("sell_orders") if steam_sell_data else None
    if max_discount is not None:
        max_discount = float(max_discount)
        if ref_price is None or ref_price <= 0:
            if log_fn:
                reason = steam_sell_error or "Steam 卖单为空或智能参考价无效"
                log_fn(f"[Buff]   → 二次验证: 无法获取 Steam 参考价：{reason}，跳过本件", "warn")
            return SKIP_VERIFICATION_FAILED
        ref_price = _adjust_ref_price_for_daily_high(
            market_hash_name, ref_price, config, log_fn, app_id=730
        )
        if lowest_price > 0:
            value_ratio = estimated_purchase_discount_ratio(lowest_price, ref_price)
            if value_ratio >= max_discount:
                if log_fn:
                    log_fn(
                        f"[Buff]   → 二次验证未通过 (Buff最低价/参考价)×1.15={value_ratio:.4f} 需<{max_discount} (参考价={ref_price:.2f})",
                        "warn",
                    )
                return SKIP_VERIFICATION_FAILED
            if log_fn:
                log_fn(
                    f"[Buff]   → 二次验证通过 (Buff最低价/参考价)×1.15={value_ratio:.4f} 参考价={ref_price:.2f}",
                    "info",
                )
    if ref_price and lowest_price > 0:
        item["value_ratio"] = estimated_purchase_discount_ratio(lowest_price, ref_price)
    n_sell_orders = int(scfg.get("sell_pressure_orders_n", 5) or 5)
    if sell_pressure_threshold is not None and sell_pressure_threshold > 0:
        daily_vol = int(item.get("daily_volume", 0) or 0)
        if daily_vol > 0 and sell_orders:
            pressure = _compute_sell_pressure_from_orders(sell_orders, daily_vol, n_sell_orders)
            if pressure is not None and pressure > sell_pressure_threshold:
                if log_fn:
                    log_fn(
                        f"[Buff]   → 卖压过高 前{n_sell_orders}档总量/日销={pressure:.2f} 阈值={sell_pressure_threshold}，跳过",
                        "warn",
                    )
                return None
        elif daily_vol <= 0 and log_fn:
            log_fn("[Buff]   → 卖压检查: 日销量为0，跳过", "info")

    # Steam/history validation can take longer than the BUFF cache TTL. Verify
    # freshness again immediately before choosing the order that will be sent
    # to the write endpoint; never lock against an old sell_order_id.
    if not _buff_orders_cache_is_fresh(item, orders_cache_ttl):
        refreshed_orders = buff_client.get_sell_orders(goods_id, game_buff)
        if not refreshed_orders:
            if log_fn:
                log_fn("[Buff]   → 锁单前卖单缓存已过期且刷新失败，停止本次购买", "warn")
            return None
        orders = refreshed_orders
        _cache_buff_sell_orders(item, orders)
        lowest_price, count_at_lowest = count_lowest_price_orders(orders)
        if lowest_price <= 0:
            if log_fn:
                log_fn("[Buff]   → 锁单前刷新得到的最低价无效，停止本次购买", "warn")
            return None
        if log_fn:
            log_fn(
                f"[Buff]   → 锁单前缓存超过 {orders_cache_ttl:.1f} 秒，已刷新为最低价={lowest_price:.2f} 数量={count_at_lowest}",
                "info",
            )
        if _affordable_quantity(target_balance, acc, lowest_price) < 1:
            return TARGET_REACHED
        if plan_price is not None and lowest_price - plan_price > tolerance:
            if log_fn:
                log_fn("[Buff]   → 锁单前刷新后价格超出容忍，停止本次购买", "warn")
            return None
        if max_discount is not None:
            if ref_price is None or ref_price <= 0:
                return SKIP_VERIFICATION_FAILED
            refreshed_ratio = estimated_purchase_discount_ratio(lowest_price, ref_price)
            if not discount_is_acceptable(refreshed_ratio, max_discount):
                if log_fn:
                    log_fn(
                        f"[Buff]   → 锁单前刷新后二次验证未通过，比例={refreshed_ratio:.4f} 需<{max_discount}",
                        "warn",
                    )
                return SKIP_VERIFICATION_FAILED
        if ref_price and lowest_price > 0:
            item["value_ratio"] = estimated_purchase_discount_ratio(lowest_price, ref_price)

    buy_runtime = ((config or {}).get("_strategy_runtime") or {}).get("buy")
    if buy_runtime:
        legacy_safe_enabled = is_strategy_module_enabled(
            config, "buy", "guard.safe_purchase_limit", default=False
        )
        hard_cap_enabled = legacy_safe_enabled or is_strategy_module_enabled(
            config, "buy", "guard.purchase_hard_cap", default=False
        )
        liquidity_cap_enabled = legacy_safe_enabled or is_strategy_module_enabled(
            config, "buy", "guard.purchase_liquidity_cap", default=False
        )
        low_price_guard_enabled = legacy_safe_enabled or is_strategy_module_enabled(
            config, "buy", "guard.low_price_purchase_guard", default=False
        )
        held_same_guard_enabled = legacy_safe_enabled or is_strategy_module_enabled(
            config, "buy", "guard.held_same_item_guard", default=False
        )
    else:
        hard_cap_enabled = True
        liquidity_cap_enabled = True
        low_price_guard_enabled = True
        held_same_guard_enabled = True
    safe_purchase_enabled = any(
        (
            hard_cap_enabled,
            liquidity_cap_enabled,
            low_price_guard_enabled,
            held_same_guard_enabled,
        )
    )
    if safe_purchase_enabled:
        cap_candidates = []
        daily_volume = int(item.get("daily_volume", 0) or 0)
        is_low_price = lowest_price < float(scfg.get("safe_purchase_low_price_threshold", 5.0))
        if hard_cap_enabled:
            cap_candidates.append(int(scfg.get("safe_purchase_hard_qty_cap", 50)))
        if liquidity_cap_enabled:
            volume_cap = int(daily_volume * float(scfg.get("safe_purchase_liquidity_ratio", 0.05)))
            if low_price_guard_enabled and is_low_price:
                volume_cap = int(
                    volume_cap * float(scfg.get("safe_purchase_low_price_penalty", 0.5))
                )
            cap_candidates.append(volume_cap)
        if low_price_guard_enabled and is_low_price:
            cap_candidates.append(int(scfg.get("safe_purchase_low_price_hard_cap", 30)))
        safe_limit = max(min(cap_candidates), 0) if cap_candidates else count_at_lowest
    else:
        safe_limit = count_at_lowest
    item_name = market_hash_name
    if item_name and held_same_guard_enabled:
        purchases_snapshot = get_purchases()
        holdings = [
            p
            for p in purchases_snapshot
            if not (p.get("sale_price") and float(p.get("sale_price", 0) or 0) > 0)
        ]
        held_same = sum(1 for p in holdings if (p.get("name") or "").strip() == item_name)
        safe_limit = max(0, safe_limit - held_same)
        if log_fn and held_same > 0:
            log_fn(
                f"[Buff]   → 已持有同名(英文) {held_same} 件，安全上限 {safe_limit + held_same} → {safe_limit}",
                "info",
            )
    if safe_limit <= 0:
        if log_fn:
            log_fn("[Buff]   → 安全采购模块限制为0，跳过本件", "warn")
        return SKIP_NO_FAILED
    affordable_quantity = _affordable_quantity(
        target_balance,
        acc,
        lowest_price,
    )
    num_to_buy = min(count_at_lowest, max(1, affordable_quantity))
    orig_num = num_to_buy
    num_to_buy = min(num_to_buy, max(1, safe_limit))
    if log_fn and orig_num > num_to_buy:
        log_fn(
            f"[Buff]   → 安全采购上限={safe_limit}，原计划={orig_num} 实际购买={num_to_buy}", "info"
        )

    def _write_snapshot_is_fresh() -> bool:
        if _buff_orders_cache_is_fresh(item, orders_cache_ttl):
            return True
        if log_fn:
            log_fn("[Buff]   → 写入前卖单快照已超过 TTL，本次不发送锁单请求", "warn")
        return False

    def _time_window_is_open() -> bool:
        if is_time_allowed is None:
            return True
        try:
            return bool(is_time_allowed())
        except Exception:
            # A broken clock/config callback must suppress a purchase write.
            return False

    ctx = PurchaseContext(
        _time_window_is_open=_time_window_is_open,
        _write_snapshot_is_fresh=_write_snapshot_is_fresh,
        acc=acc,
        append_purchase=append_purchase,
        buff_cfg=buff_cfg,
        buff_client=buff_client,
        config=config,
        confirm_payment=confirm_payment,
        game_buff=game_buff,
        goods_id=goods_id,
        is_stop_requested=is_stop_requested,
        item=item,
        log_fn=log_fn,
        lowest_price=lowest_price,
        num_to_buy=num_to_buy,
        on_entering_payment=on_entering_payment,
        orders=orders,
        ref_price=ref_price,
        set_pending_payment=set_pending_payment,
        wait_payment_confirm=wait_payment_confirm,
    )

    if num_to_buy == 1:
        return _finish_attempt(_try_single_buy(ctx))

    batch_attempt = _try_batch_buy(ctx)
    if batch_attempt.status is _PurchaseAttemptStatus.SAFE_TO_FALLBACK:
        if log_fn:
            log_fn("[Buff]   → 批量购买明确不支持/未创建，安全降级为单件购买", "info")
        return _finish_attempt(_try_single_buy(ctx))
    return _finish_attempt(batch_attempt)
