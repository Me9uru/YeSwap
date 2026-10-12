"""Purchase selection responsibility."""

from __future__ import annotations
from app.strategies.rules import discount_is_acceptable
from typing import Any, Callable, Dict, List, Optional, Set, Tuple
from app.runtime.state import set_status
from app.strategies.expressions import evaluate_strategy_runtime_modules
from app.strategies.runtime import is_strategy_module_enabled
from app.services.steam_client import SteamClient
from app.services.analysis_client import StabilityAnalyzer
from app.services.delay import jittered_sleep
from utils.money import estimated_purchase_discount_ratio
from app.purchase.market import (
    _adjust_ref_price_for_daily_high,
    _check_buff_price,
    _compute_sell_pressure_from_orders,
    _fetch_steam_sell_data,
    _goods_id_from_buff_url,
    _parse_threshold,
)


SKIP_NO_FAILED = object()


_RATIO_ATTR = {"sell": "sell_ratio", "buy": "buy_ratio"}


def _log_stability_rejection(
    report: dict,
    stability_cfg: dict,
    smart_price,
    log_fn,
) -> None:
    # 打印一行可读的拒绝原因，方便排查为什么这件东西被跳过
    if not log_fn:
        return
    msg = report.get("msg", "指标验证不通过")
    st = report.get("status", "")
    cv = report.get("cv", 0)
    r2 = report.get("r_squared", 0)
    avg = report.get("avg", 0)
    slope = report.get("slope", 0)
    pp = report.get("price_percentile")
    pp_str = f" 分位={pp:.2f}" if pp is not None else ""
    smart_str = f" 智能选价={smart_price:.2f}" if smart_price is not None else ""
    ma_str = f" EMA7={report.get('ma7', 0):.2f} EMA30={report.get('ma30', 0):.2f}"
    bb_upper = report.get("bb_upper")
    bb_str = f" BB+={bb_upper:.2f}" if bb_upper is not None else ""
    log_fn(
        f"[稳定性]   → 拒绝: {msg} status={st} cv={cv:.3f} R2={r2:.3f} 均价={avg:.2f} slope={slope:.4f}{ma_str}{bb_str}{smart_str}{pp_str}",
        "warn",
    )


def filter_iflow_rows(
    rows: List[Any],
    config: dict,
    log_fn: Optional[Callable[[str, str], None]] = None,
) -> List[Dict[str, Any]]:
    pipeline_cfg = config.get("pipeline", {})
    iflow_cfg = config.get("steamdt") or config.get("iflow", {})
    exclude = pipeline_cfg.get("exclude_keywords", [])
    top_n = int(pipeline_cfg.get("iflow_top_n", 0) or 0)
    if top_n > 0:
        rows = rows[:top_n]
    sort_by = (iflow_cfg.get("sort_by") or "sell").strip()
    ratio_attr = _RATIO_ATTR.get(sort_by, "sell_ratio")
    steam_client = SteamClient()
    filtered = []
    skipped_keyword = 0
    skipped_price = 0
    skipped_no_buff = 0
    for r in rows:
        name = (getattr(r, "name", None) or "").lower()
        name_cn = (getattr(r, "name_cn", None) or "").lower()
        if any(kw in name or kw in name_cn for kw in exclude):
            skipped_keyword += 1
            continue
        try:
            price = float(getattr(r, "min_price", 0))
        except (ValueError, TypeError):
            skipped_price += 1
            continue
        if price <= 0:
            skipped_price += 1
            continue
        try:
            ratio_val = float(getattr(r, ratio_attr, 0) or 0)
        except (ValueError, TypeError):
            ratio_val = 0
        gid = _goods_id_from_buff_url(getattr(r, "platform", "") or "")
        if gid <= 0:
            skipped_no_buff += 1
            continue
        steam_link = getattr(r, "steam_link", None) or ""
        steam_market_name = steam_client.market_hash_name_from_listing_url(steam_link) or name
        try:
            vol = int(getattr(r, "volume", "0") or 0)
        except (ValueError, TypeError):
            vol = 0
        filtered.append(
            {
                "name": getattr(r, "name", ""),
                "min_price": price,
                "goods_id": gid,
                "platform": getattr(r, "platform", ""),
                "steam_market_name": steam_market_name,
                "steam_link": steam_link,
                "ratio": ratio_val,
                "daily_volume": vol,
            }
        )
    if log_fn:
        parts = [f"排除关键词={skipped_keyword}", f"价格无效={skipped_price}"]
        if top_n > 0:
            parts.append(f"取前{top_n}条")
        parts.extend([f"非Buff链接={skipped_no_buff}", f"→ 通过 {len(filtered)} 条"])
        log_fn(f"[筛选] {' '.join(parts)}", "info")
    return filtered


def _check_sell_pressure_precheck(
    item,
    steam_sell_data,
    sell_pressure_threshold,
    pipeline_cfg,
    log_fn,
) -> bool:
    # 检查卖压是否过高
    if sell_pressure_threshold is not None and sell_pressure_threshold > 0:
        daily_vol = int(item.get("daily_volume", 0) or 0)
        sell_orders = steam_sell_data.get("sell_orders")
        n_sell_orders = int(pipeline_cfg.get("sell_pressure_orders_n", 5) or 5)
        if daily_vol > 0 and sell_orders:
            pressure = _compute_sell_pressure_from_orders(sell_orders, daily_vol, n_sell_orders)
            if pressure is not None and pressure > sell_pressure_threshold:
                if log_fn:
                    log_fn(
                        f"[稳定性]   → 预检未通过: 卖压过高 前{n_sell_orders}档总量/日销={pressure:.2f} 阈值={sell_pressure_threshold}",
                        "warn",
                    )
                return False
        elif daily_vol <= 0 and log_fn:
            log_fn("[稳定性]   → 卖压检查: 日销量为0，跳过", "info")
    return True


def _check_max_discount_precheck(
    item,
    gid,
    smart_price,
    est_ratio,
    ref_price_est,
    plan_price,
    max_discount,
    log_fn,
) -> bool:
    # 检查买入价占Steam参考价的比例是否低于max_discount，超了就不够利润
    if max_discount is not None:
        max_discount_float = float(max_discount)
        if smart_price is None or smart_price <= 0:
            if log_fn:
                log_fn("[稳定性]   → 预检未通过: Steam 卖单已返回，但智能参考价为空或无效", "warn")
            return False
        if est_ratio is None or est_ratio <= 0:
            if log_fn:
                plan_str = f"{plan_price:.2f}" if isinstance(plan_price, (int, float)) else "无效"
                ref_str = (
                    f"{ref_price_est:.2f}" if isinstance(ref_price_est, (int, float)) else "无效"
                )
                log_fn(
                    f"[稳定性]   → 预检未通过: 无法计算预估比例 (Buff最低价={plan_str}, Steam参考价={ref_str})",
                    "warn",
                )
            return False
        if not discount_is_acceptable(est_ratio, max_discount_float):
            if log_fn:
                log_fn(
                    f"[稳定性]   → 预检未通过: (Buff最低价/Steam参考价)×1.15={est_ratio:.4f} 需<{max_discount_float} (Steam参考价={ref_price_est:.2f})",
                    "warn",
                )
            return False
    return True


def _passes_final_buff_precheck(
    item: Dict[str, Any],
    gid: int,
    config: dict,
    buff_client: Optional[Any],
    smart_price: Optional[float],
    ref_price_est: Optional[float],
    max_discount: Optional[float],
    log_fn: Optional[Callable[[str, str], None]],
) -> bool:
    """Fetch BUFF only after the candidate passed the non-BUFF guards."""
    if not buff_client or not is_strategy_module_enabled(config, "buy", "buy.buff_realtime_price"):
        return True
    buff_ok, realtime_price = _check_buff_price(
        item,
        gid,
        item.get("min_price"),
        buff_client,
        config,
        log_fn,
    )
    if not buff_ok:
        return False

    # The earlier discount check used the feed price. Re-check with the actual
    # BUFF snapshot so a small but threshold-crossing price rise is still safe.
    if max_discount is not None:
        realtime_ratio = None
        if ref_price_est is not None and ref_price_est > 0 and realtime_price is not None:
            realtime_ratio = estimated_purchase_discount_ratio(realtime_price, ref_price_est)
        if not _check_max_discount_precheck(
            item,
            gid,
            smart_price,
            realtime_ratio,
            ref_price_est,
            realtime_price,
            max_discount,
            log_fn,
        ):
            return False
    return True


def _build_buy_strategy_outputs(
    item: Dict[str, Any],
    steam_sell_data: Optional[Dict[str, Any]] = None,
    smart_price: Optional[float] = None,
    est_ratio: Optional[float] = None,
    ref_price_est: Optional[float] = None,
    report: Optional[Dict[str, Any]] = None,
    pipeline_cfg: Optional[dict] = None,
) -> Dict[str, Any]:
    outputs: Dict[str, Any] = {}
    if steam_sell_data:
        orders = steam_sell_data.get("sell_orders") or []
        daily_volume = int(item.get("daily_volume", 0) or 0)
        sell_pressure = None
        if daily_volume > 0 and orders:
            try:
                n = int((pipeline_cfg or {}).get("sell_pressure_orders_n", 5) or 5)
                top_orders = orders[: max(1, n)]
                volume = sum(
                    int(o.get("quantity", 0) or 0) for o in top_orders if isinstance(o, dict)
                )
                sell_pressure = volume / daily_volume
            except Exception:
                sell_pressure = None
        outputs["buy.steam_sell_depth"] = {
            "smart_price": smart_price
            if smart_price is not None
            else steam_sell_data.get("smart_price"),
            "sell_orders_count": len(orders),
            "sell_pressure": sell_pressure,
            "reference_price": ref_price_est,
            "estimated_ratio": est_ratio,
        }
    if report:
        outputs["guard.history_data_window"] = {
            key: report.get(key)
            for key in (
                "status",
                "avg",
                "cv",
                "r_squared",
                "slope",
                "price_percentile",
                "ma7",
                "ma30",
                "is_stable",
            )
        }
    if est_ratio is not None:
        outputs["guard.max_discount"] = {
            "estimated_ratio": est_ratio,
            "limit": (pipeline_cfg or {}).get("max_discount"),
        }
    return outputs


def _passes_custom_buy_modules(
    item: Dict[str, Any],
    config: dict,
    *,
    steam_sell_data: Optional[Dict[str, Any]] = None,
    smart_price: Optional[float] = None,
    est_ratio: Optional[float] = None,
    ref_price_est: Optional[float] = None,
    report: Optional[Dict[str, Any]] = None,
    log_fn: Optional[Callable[[str, str], None]] = None,
) -> bool:
    outputs = _build_buy_strategy_outputs(
        item,
        steam_sell_data=steam_sell_data,
        smart_price=smart_price,
        est_ratio=est_ratio,
        ref_price_est=ref_price_est,
        report=report,
        pipeline_cfg=config.get("pipeline") or {},
    )
    context = {
        "item": item,
        "config": config,
    }
    results, blocking = evaluate_strategy_runtime_modules(
        config,
        "buy",
        "buy.candidate_guard",
        context=context,
        outputs=outputs,
    )
    if log_fn:
        for result in results:
            level = "warn" if result.get("status") in {"reject", "error"} else "info"
            log_fn(
                f"[策略模块] {result.get('module_name')}: {result.get('reason')} ({result.get('status')})",
                level,
            )
    return blocking is None


def pick_stable_item(
    filtered: List[Dict[str, Any]],
    config: dict,
    steam_client: SteamClient,
    analyzer: StabilityAnalyzer,
    is_stop_requested: callable,
    log_fn: Optional[Callable[[str, str], None]] = None,
    exclude_goods_ids: Optional[set] = None,
    buff_client: Optional[Any] = None,
) -> Tuple[Optional[Dict[str, Any]], Set[int]]:
    # 遍历候选饰品，返回第一个通过所有检测的
    # 检测顺序: Buff价格预检 → Steam卖单 → 卖压 → 最高折扣 → 历史稳定性
    stability_cfg = config.get("stability", {})
    stability_days = int(stability_cfg.get("days", 30))
    cv_threshold = float(stability_cfg.get("cv_threshold", 0.05))
    r2_threshold = float(stability_cfg.get("r2_threshold", 0.6))
    min_daily_trades = float(stability_cfg.get("min_daily_trades", 5))
    excluded = exclude_goods_ids or set()
    stability_failed: Set[int] = set()
    n = len(filtered)
    request_interval = float(stability_cfg.get("request_interval_seconds", 2.5))
    failure_delay = max(0, float(stability_cfg.get("request_failure_delay_seconds", 5) or 5))
    legacy_history_enabled = is_strategy_module_enabled(
        config, "buy", "guard.history_stability", default=False
    )
    history_data_enabled = legacy_history_enabled or is_strategy_module_enabled(
        config, "buy", "guard.history_data_window"
    )
    volatility_enabled = legacy_history_enabled or is_strategy_module_enabled(
        config, "buy", "guard.volatility_cv"
    )
    trend_quality_enabled = legacy_history_enabled or is_strategy_module_enabled(
        config, "buy", "guard.trend_quality"
    )
    price_position_enabled = legacy_history_enabled or is_strategy_module_enabled(
        config, "buy", "guard.price_position"
    )
    history_analysis_enabled = any(
        (
            legacy_history_enabled,
            history_data_enabled,
            volatility_enabled,
            trend_quality_enabled,
            price_position_enabled,
        )
    )
    for i, item in enumerate(filtered):
        if is_stop_requested():
            return None, stability_failed
        gid = item.get("goods_id")
        if gid is not None and gid in excluded:
            continue
        if i > 0 and request_interval > 0:
            jittered_sleep(request_interval)
        name = item.get("name", "")
        market_hash_name = item.get("steam_market_name") or name
        # 带饰品名称前缀的日志包装，方便追踪每条日志对应哪个饰品
        _short = (name[:30] + "…") if len(name) > 30 else name
        item_log = (
            (lambda msg, level, _n=_short: log_fn(f"[{_n}] {msg}", level)) if log_fn else None
        )
        next_name = filtered[i + 1].get("name", "") if i + 1 < n else ""
        set_status(
            "running",
            step="STABILITY_CHECK",
            progress_total=n,
            progress_done=i,
            progress_item=f"({i + 1}/{n}) {name}",
            next_progress_item=f"({i + 2}/{n}) {next_name}" if next_name else "",
        )
        pipeline_cfg = config.get("pipeline", {})
        verbose_detail = bool(pipeline_cfg.get("verbose_debug", False))
        if item_log and verbose_detail:
            item_log(
                f"[稳定性] 开始预检 ({i + 1}/{n}) Buff参考价={item.get('min_price')} 比例={item.get('ratio')}",
                "debug",
            )
        max_discount = (
            pipeline_cfg.get("max_discount")
            if is_strategy_module_enabled(config, "buy", "guard.max_discount")
            else None
        )
        sell_pressure_threshold = (
            _parse_threshold(pipeline_cfg.get("sell_pressure_threshold"))
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
        steam_sell_data: Optional[Dict[str, Any]] = None
        smart_price: Optional[float] = None
        est_ratio: Optional[float] = None
        ref_price_est: Optional[float] = None

        if need_steam:
            plan_price = item.get("min_price")

            # 1. 拉取 Steam 挂单数据。BUFF 实时预检延后到所有非 BUFF
            # 策略通过之后，避免对注定淘汰的候选消耗请求。
            if item_log and verbose_detail:
                item_log("[稳定性] 拉取 Steam 卖单深度…", "debug")
            steam_sell_data, steam_error = _fetch_steam_sell_data(
                market_hash_name, config, app_id=730, return_error=True
            )
            if not steam_sell_data:
                if item_log:
                    item_log(
                        f"[稳定性] 预检未通过: 无法获取 Steam 卖单信息：{steam_error or '未知原因'}",
                        "warn",
                    )
                if gid:
                    stability_failed.add(gid)
                if failure_delay > 0:
                    jittered_sleep(failure_delay)
                continue
            if item_log and verbose_detail:
                orders_count = len(steam_sell_data.get("sell_orders") or [])
                smart_dbg = steam_sell_data.get("smart_price")
                smart_str = f"{smart_dbg:.2f}" if isinstance(smart_dbg, (int, float)) else "无"
                item_log(
                    f"[稳定性] Steam 卖单获取成功: {orders_count} 档 智能参考价={smart_str}",
                    "debug",
                )

            # 2. 卖压检测
            if not _check_sell_pressure_precheck(
                item, steam_sell_data, sell_pressure_threshold, pipeline_cfg, item_log
            ):
                if gid:
                    stability_failed.add(gid)
                if failure_delay > 0:
                    jittered_sleep(failure_delay)
                continue

            # 3. 计算智能价和预估比例
            smart_price = steam_sell_data.get("smart_price")
            if smart_price is not None and smart_price > 0 and plan_price and plan_price > 0:
                ref_price_est = _adjust_ref_price_for_daily_high(
                    market_hash_name, smart_price, config, log_fn, app_id=730
                )
                est_ratio = estimated_purchase_discount_ratio(plan_price, ref_price_est)

            # 4. 最高折扣检测
            if not _check_max_discount_precheck(
                item, gid, smart_price, est_ratio, ref_price_est, plan_price, max_discount, item_log
            ):
                if gid:
                    stability_failed.add(gid)
                if failure_delay > 0:
                    jittered_sleep(failure_delay)
                continue

        if not history_analysis_enabled:
            if smart_price is None and not need_steam:
                steam_sell_data = _fetch_steam_sell_data(market_hash_name, config, app_id=730)
                smart_price = steam_sell_data.get("smart_price") if steam_sell_data else None
            item["_steam_sell_data"] = steam_sell_data
            if not _passes_custom_buy_modules(
                item,
                config,
                steam_sell_data=steam_sell_data,
                smart_price=smart_price,
                est_ratio=est_ratio,
                ref_price_est=ref_price_est,
                log_fn=item_log,
            ):
                if gid:
                    stability_failed.add(gid)
                continue
            if not _passes_final_buff_precheck(
                item,
                gid,
                config,
                buff_client,
                smart_price,
                ref_price_est,
                max_discount,
                item_log,
            ):
                if gid:
                    stability_failed.add(gid)
                continue
            if item_log:
                item_log("[稳定性] 历史稳定性模块未启用，跳过历史分析，选定本件", "info")
            return item, stability_failed

        # 6. 拉历史K线 + 稳定性分析
        if item_log:
            item_log("[稳定性] 拉取历史价格…", "info")
        raw = steam_client.fetch_history(market_hash_name, return_currency=True)
        if raw and isinstance(raw, dict):
            history = raw.get("history")
            currency = raw.get("currency")
        else:
            history = raw if isinstance(raw, list) else None
            currency = None
        if not history:
            if gid:
                stability_failed.add(gid)
            if item_log:
                item_log("[稳定性] 无历史数据或请求失败，试下一个", "warn")
            if failure_delay > 0:
                jittered_sleep(failure_delay)
            continue

        # 利润特别大时适当放宽价格分位限制
        dyn_price_percentile_ceil = (
            float(stability_cfg.get("price_percentile_ceil", 0.8))
            if price_position_enabled
            else 999.0
        )
        if est_ratio is not None and est_ratio > 0 and max_discount is not None:
            max_discount_float = float(max_discount)
            huge_offset = float(pipeline_cfg.get("huge_profit_offset", 0.05))
            huge_ratio = max_discount_float - huge_offset
            high_ratio = max_discount_float - (huge_offset / 2.0)
            if est_ratio < huge_ratio:
                dyn_price_percentile_ceil = 0.88
                if item_log:
                    item_log(
                        f"[稳定性] 检测到巨额预期利润 (比例={est_ratio:.4f} < {huge_ratio:.4f})，放宽价格分位点限制至 {dyn_price_percentile_ceil}",
                        "info",
                    )
            elif est_ratio < high_ratio:
                dyn_price_percentile_ceil = max(dyn_price_percentile_ceil, 0.85)
                if item_log:
                    item_log(
                        f"[稳定性] 检测到极高预期利润 (比例={est_ratio:.4f} < {high_ratio:.4f})，放宽价格分位点限制至 {dyn_price_percentile_ceil}",
                        "info",
                    )

        report = analyzer.analyze(
            history,
            days=stability_days,
            currency=currency,
            cv_threshold=cv_threshold if volatility_enabled else 999.0,
            r2_threshold=r2_threshold if trend_quality_enabled else 2.0,
            min_daily_trades=min_daily_trades if history_data_enabled else 0,
            current_price=smart_price,
            price_percentile_ceil=dyn_price_percentile_ceil,
            r2_rising_threshold=float(stability_cfg.get("r2_rising_threshold", 0.8))
            if trend_quality_enabled
            else -1.0,
            slope_pct_ceil=float(stability_cfg.get("slope_pct_ceil", 0.01))
            if trend_quality_enabled
            else 999.0,
            ma_deviation_ceil=float(stability_cfg.get("ma_deviation_ceil", 1.1))
            if price_position_enabled
            else 999.0,
            last_price_ma30_ceil=float(stability_cfg.get("last_price_ma30_ceil", 1.05))
            if price_position_enabled
            else 999.0,
            slope_stable_floor=float(stability_cfg.get("slope_stable_floor", -0.005))
            if trend_quality_enabled
            else -999.0,
            price_percentile_ceil_rising=float(
                stability_cfg.get("price_percentile_ceil_rising", 0.5)
            )
            if price_position_enabled
            else 999.0,
            use_vwap=bool(stability_cfg.get("use_vwap", True)),
        )
        if smart_price is not None and not report.get("valid"):
            if gid:
                stability_failed.add(gid)
            if item_log:
                item_log(f"[稳定性] 分析异常: {report.get('msg', '无效')}，试下一个", "warn")
            if failure_delay > 0:
                jittered_sleep(failure_delay)
            continue

        if not report.get("is_stable"):
            _log_stability_rejection(report, stability_cfg, smart_price, item_log)
            if gid:
                stability_failed.add(gid)
            if failure_delay > 0:
                jittered_sleep(failure_delay)
            continue
        if smart_price is None and not need_steam:
            steam_sell_data = _fetch_steam_sell_data(market_hash_name, config, app_id=730)
            smart_price = steam_sell_data.get("smart_price") if steam_sell_data else None
        item["_steam_sell_data"] = steam_sell_data
        if not _passes_custom_buy_modules(
            item,
            config,
            steam_sell_data=steam_sell_data,
            smart_price=smart_price,
            est_ratio=est_ratio,
            ref_price_est=ref_price_est,
            report=report,
            log_fn=item_log,
        ):
            if gid:
                stability_failed.add(gid)
            if failure_delay > 0:
                jittered_sleep(failure_delay)
            continue
        if not _passes_final_buff_precheck(
            item,
            gid,
            config,
            buff_client,
            smart_price,
            ref_price_est,
            max_discount,
            item_log,
        ):
            if gid:
                stability_failed.add(gid)
            continue
        if item_log:
            st = report.get("status", "")
            sl = report.get("slope", 0)
            r2 = report.get("r_squared", 0)
            pp = report.get("price_percentile")
            pp_str = f" 分位={pp:.2f}" if pp is not None else ""
            smart_str = f" 智能选价={smart_price:.2f}" if smart_price is not None else ""
            ma_str = f" EMA7={report.get('ma7', 0):.2f} EMA30={report.get('ma30', 0):.2f}"
            bb_upper = report.get("bb_upper")
            bb_str = f" BB+={bb_upper:.2f}" if bb_upper is not None else ""
            item_log(
                f"[稳定性] ✓ 通过 status={st} cv={report.get('cv', 0):.3f} R2={r2:.3f} 均价={report.get('avg', 0):.2f} slope={sl:.4f}{ma_str}{bb_str}{smart_str}{pp_str}，选定本件",
                "info",
            )
        return item, stability_failed
    return None, stability_failed
