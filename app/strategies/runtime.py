"""Strategy runtime responsibility."""

import copy
from typing import Any, Dict, List, Optional
from app.configuration.repository import load_app_config_validated
from app.strategies.catalog import (
    SELL_LEGACY_TO_SYSTEM,
)
from app.strategies.registry import (
    _module_by_id,
    get_strategy,
)


def _legacy_sell_id(config: dict) -> str:
    try:
        n = int((config.get("pipeline") or {}).get("sell_strategy", 1))
    except Exception:
        n = 1
    return SELL_LEGACY_TO_SYSTEM.get(n, "system.sell.pause")


def get_active_strategy_ids(config: Optional[dict] = None) -> Dict[str, str]:
    cfg = config or load_app_config_validated()
    scfg = cfg.get("strategies") or {}
    buy_id = scfg.get("active_buy_strategy_id") or "system.buy.default"
    sell_id = scfg.get("active_sell_strategy_id") or _legacy_sell_id(cfg)
    if not get_strategy(buy_id) or get_strategy(buy_id).get("strategy_type") != "buy":
        buy_id = "system.buy.default"
    if not get_strategy(sell_id) or get_strategy(sell_id).get("strategy_type") != "sell":
        sell_id = _legacy_sell_id(cfg)
    return {"buy": buy_id, "sell": sell_id}


def _enabled_steps(strategy: dict) -> List[dict]:
    return [s for s in strategy.get("steps") or [] if s.get("enabled") is not False]


def _current_param_values(config: dict) -> Dict[str, Dict[str, Any]]:
    pipe = config.get("pipeline") or {}
    stability = config.get("stability") or {}
    return {
        "buy.steamdt_top_n": {"iflow_top_n": pipe.get("iflow_top_n")},
        "buy.exclude_keywords": {"exclude_keywords": pipe.get("exclude_keywords")},
        "guard.max_discount": {"max_discount": pipe.get("max_discount")},
        "guard.sell_pressure": {
            "sell_pressure_orders_n": pipe.get("sell_pressure_orders_n"),
            "sell_pressure_threshold": pipe.get("sell_pressure_threshold"),
        },
        "guard.history_stability": {
            "days": stability.get("days"),
            "cv_threshold": stability.get("cv_threshold"),
            "r2_threshold": stability.get("r2_threshold"),
            "min_daily_trades": stability.get("min_daily_trades"),
            "price_percentile_ceil": stability.get("price_percentile_ceil"),
            "r2_rising_threshold": stability.get("r2_rising_threshold"),
            "slope_pct_ceil": stability.get("slope_pct_ceil"),
            "ma_deviation_ceil": stability.get("ma_deviation_ceil"),
            "last_price_ma30_ceil": stability.get("last_price_ma30_ceil"),
            "slope_stable_floor": stability.get("slope_stable_floor"),
            "price_percentile_ceil_rising": stability.get("price_percentile_ceil_rising"),
            "use_vwap": stability.get("use_vwap"),
        },
        "guard.history_data_window": {
            "days": stability.get("days"),
            "min_daily_trades": stability.get("min_daily_trades"),
            "use_vwap": stability.get("use_vwap"),
        },
        "guard.volatility_cv": {"cv_threshold": stability.get("cv_threshold")},
        "guard.trend_quality": {
            "r2_threshold": stability.get("r2_threshold"),
            "r2_rising_threshold": stability.get("r2_rising_threshold"),
            "slope_pct_ceil": stability.get("slope_pct_ceil"),
            "slope_stable_floor": stability.get("slope_stable_floor"),
        },
        "guard.price_position": {
            "price_percentile_ceil": stability.get("price_percentile_ceil"),
            "price_percentile_ceil_rising": stability.get("price_percentile_ceil_rising"),
            "ma_deviation_ceil": stability.get("ma_deviation_ceil"),
            "last_price_ma30_ceil": stability.get("last_price_ma30_ceil"),
        },
        "guard.safe_purchase_limit": {
            "safe_purchase_hard_qty_cap": pipe.get("safe_purchase_hard_qty_cap"),
            "safe_purchase_liquidity_ratio": pipe.get("safe_purchase_liquidity_ratio"),
            "safe_purchase_low_price_threshold": pipe.get("safe_purchase_low_price_threshold"),
            "safe_purchase_low_price_penalty": pipe.get("safe_purchase_low_price_penalty"),
            "safe_purchase_low_price_hard_cap": pipe.get("safe_purchase_low_price_hard_cap"),
        },
        "guard.purchase_hard_cap": {
            "safe_purchase_hard_qty_cap": pipe.get("safe_purchase_hard_qty_cap"),
        },
        "guard.purchase_liquidity_cap": {
            "safe_purchase_liquidity_ratio": pipe.get("safe_purchase_liquidity_ratio"),
        },
        "guard.low_price_purchase_guard": {
            "safe_purchase_low_price_threshold": pipe.get("safe_purchase_low_price_threshold"),
            "safe_purchase_low_price_penalty": pipe.get("safe_purchase_low_price_penalty"),
            "safe_purchase_low_price_hard_cap": pipe.get("safe_purchase_low_price_hard_cap"),
        },
        "guard.held_same_item_guard": {},
        "guard.target_balance": {"target_balance": pipe.get("target_balance")},
        "guard.max_listings_per_item": {"max_listings_per_item": pipe.get("max_listings_per_item")},
        "pricing.steam_wall_gap": {
            "sell_price_wall_volume": pipe.get("sell_price_wall_volume"),
            "sell_price_max_ignore_volume": pipe.get("sell_price_max_ignore_volume"),
            "sell_price_offset": pipe.get("sell_price_offset"),
        },
        "pricing.steam_wall_price": {
            "sell_price_wall_volume": pipe.get("sell_price_wall_volume"),
            "sell_price_max_ignore_volume": pipe.get("sell_price_max_ignore_volume"),
        },
        "pricing.price_offset": {"sell_price_offset": pipe.get("sell_price_offset")},
        "guard.rising_trend_wait": {"sell_trend_days": pipe.get("sell_trend_days")},
        "guard.profit_ratio": {
            "profit_ratio_multiplier": pipe.get("profit_ratio_multiplier", 1.05)
        },
    }


def _hydrate_strategy_params(strategy: dict, config: dict) -> dict:
    out = copy.deepcopy(strategy)
    current = _current_param_values(config)
    modules = _module_by_id()
    for step in out.get("steps") or []:
        module_id = step.get("module_id")
        params = step.get("params") if isinstance(step.get("params"), dict) else {}
        hydrated = {}
        schema = (modules.get(module_id) or {}).get("params_schema") or {}
        for key, meta in schema.items():
            val = params.get(key)
            if val is None:
                val = (current.get(module_id) or {}).get(key)
            if val is None:
                val = meta.get("default") if isinstance(meta, dict) else None
            if val is not None:
                hydrated[key] = val
        hydrated.update({k: v for k, v in params.items() if v is not None})
        step["params"] = hydrated
    return out


def _step_params(strategy: dict, module_id: str) -> dict:
    for s in strategy.get("steps") or []:
        if s.get("module_id") == module_id and s.get("enabled") is not False:
            return s.get("params") if isinstance(s.get("params"), dict) else {}
    return {}


def _enabled_module_ids(strategy: dict) -> set:
    return {s.get("module_id") for s in _enabled_steps(strategy)}


def _derive_sell_strategy_number(strategy: dict) -> int:
    enabled = _enabled_module_ids(strategy)
    if "action.pause_auto_sell" in enabled or "action.steam_list" not in enabled:
        return 4
    if "guard.profit_ratio" in enabled:
        return 3
    if "guard.rising_trend_wait" in enabled:
        return 2
    return 1


def apply_strategy_to_config(
    config: dict, strategy_type: str, strategy_override: Optional[dict] = None
) -> dict:
    cfg = copy.deepcopy(config)
    if strategy_override is not None:
        strategy = copy.deepcopy(strategy_override)
        active_id = strategy.get("id") or get_active_strategy_ids(cfg).get(strategy_type)
    else:
        active_id = get_active_strategy_ids(cfg).get(strategy_type)
        strategy = get_strategy(active_id)
    if not strategy or strategy.get("strategy_type") != strategy_type:
        return cfg
    strategy = _hydrate_strategy_params(strategy, cfg)
    enabled_ids = _enabled_module_ids(strategy)
    params_by_module = {
        s.get("module_id"): (s.get("params") if isinstance(s.get("params"), dict) else {})
        for s in strategy.get("steps") or []
    }
    modules = _module_by_id()
    enabled_steps = copy.deepcopy(_enabled_steps(strategy))
    runtime_modules = {
        module_id: copy.deepcopy(modules.get(module_id) or {})
        for module_id in enabled_ids
        if modules.get(module_id)
    }
    runtime = cfg.setdefault("_strategy_runtime", {})
    runtime[strategy_type] = {
        "active_strategy_id": active_id,
        "enabled_modules": sorted(enabled_ids),
        "params": copy.deepcopy(params_by_module),
        "steps": enabled_steps,
        "modules": runtime_modules,
    }
    pipe = cfg.setdefault("pipeline", {})
    stability = cfg.setdefault("stability", {})
    if strategy_type == "buy":
        if "buy.steamdt_top_n" in enabled_ids:
            params = _step_params(strategy, "buy.steamdt_top_n")
            if "iflow_top_n" in params:
                pipe["iflow_top_n"] = int(params.get("iflow_top_n") or 0)
        else:
            pipe["iflow_top_n"] = 0
        if "buy.exclude_keywords" in enabled_ids:
            params = _step_params(strategy, "buy.exclude_keywords")
            if isinstance(params.get("exclude_keywords"), list):
                pipe["exclude_keywords"] = [
                    str(x) for x in params.get("exclude_keywords") if str(x).strip()
                ]
        else:
            pipe["exclude_keywords"] = []
        if "guard.max_discount" in enabled_ids:
            params = _step_params(strategy, "guard.max_discount")
            if "max_discount" in params:
                pipe["max_discount"] = float(params.get("max_discount"))
        else:
            pipe["max_discount"] = None
        if "guard.sell_pressure" in enabled_ids:
            params = _step_params(strategy, "guard.sell_pressure")
            if "sell_pressure_orders_n" in params:
                pipe["sell_pressure_orders_n"] = int(params.get("sell_pressure_orders_n") or 5)
            if "sell_pressure_threshold" in params:
                pipe["sell_pressure_threshold"] = float(params.get("sell_pressure_threshold") or 0)
        else:
            pipe["sell_pressure_threshold"] = None
        if "guard.history_stability" in enabled_ids:
            for key, val in _step_params(strategy, "guard.history_stability").items():
                if key in stability:
                    stability[key] = val
        for module_id in (
            "guard.history_data_window",
            "guard.volatility_cv",
            "guard.trend_quality",
            "guard.price_position",
        ):
            if module_id in enabled_ids:
                for key, val in _step_params(strategy, module_id).items():
                    if key in stability:
                        stability[key] = val
        for module_id in (
            "guard.safe_purchase_limit",
            "guard.purchase_hard_cap",
            "guard.purchase_liquidity_cap",
            "guard.low_price_purchase_guard",
        ):
            if module_id in enabled_ids:
                for key, val in _step_params(strategy, module_id).items():
                    pipe[key] = val
        if "guard.target_balance" in enabled_ids:
            params = _step_params(strategy, "guard.target_balance")
            if "target_balance" in params:
                pipe["target_balance"] = float(
                    params.get("target_balance") or pipe.get("target_balance", 100)
                )
    else:
        pipe["sell_strategy"] = _derive_sell_strategy_number(strategy)
        if "guard.max_listings_per_item" in enabled_ids:
            params = _step_params(strategy, "guard.max_listings_per_item")
            if "max_listings_per_item" in params:
                pipe["max_listings_per_item"] = int(params.get("max_listings_per_item") or 5)
        if "pricing.steam_wall_gap" in enabled_ids:
            for key, val in _step_params(strategy, "pricing.steam_wall_gap").items():
                pipe[key] = val
        if "pricing.steam_wall_price" in enabled_ids:
            for key, val in _step_params(strategy, "pricing.steam_wall_price").items():
                pipe[key] = val
            if (
                "pricing.price_offset" not in enabled_ids
                and "pricing.steam_wall_gap" not in enabled_ids
            ):
                pipe["sell_price_offset"] = 0
        if "pricing.price_offset" in enabled_ids:
            params = _step_params(strategy, "pricing.price_offset")
            if "sell_price_offset" in params:
                pipe["sell_price_offset"] = float(params.get("sell_price_offset") or 0)
        if "guard.rising_trend_wait" in enabled_ids:
            params = _step_params(strategy, "guard.rising_trend_wait")
            if "sell_trend_days" in params:
                pipe["sell_trend_days"] = int(params.get("sell_trend_days") or 7)
        if "guard.profit_ratio" in enabled_ids:
            params = _step_params(strategy, "guard.profit_ratio")
            if "profit_ratio_multiplier" in params:
                pipe["profit_ratio_multiplier"] = float(
                    params.get("profit_ratio_multiplier") or 1.05
                )
    return cfg


def is_strategy_module_enabled(
    config: dict, strategy_type: str, module_id: str, default: bool = True
) -> bool:
    runtime = ((config or {}).get("_strategy_runtime") or {}).get(strategy_type)
    if not runtime:
        return default
    return module_id in set(runtime.get("enabled_modules") or [])


def get_strategy_module_params(config: dict, strategy_type: str, module_id: str) -> dict:
    runtime = ((config or {}).get("_strategy_runtime") or {}).get(strategy_type) or {}
    params = (runtime.get("params") or {}).get(module_id)
    return params if isinstance(params, dict) else {}
