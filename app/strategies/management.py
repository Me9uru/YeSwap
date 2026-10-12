"""Strategy management responsibility."""

import copy
from typing import Any, Dict
from app.configuration.repository import load_app_config_validated, save_app_config_validated
from app.strategies.module_specs import BUILTIN_MODULES
from app.strategies.catalog import (
    SELL_SYSTEM_TO_LEGACY,
    STRATEGY_STEP_LIMITS,
    StrategyError,
)
from app.strategies.expressions import (
    _available_data_catalog,
)
from app.strategies.runtime import (
    _hydrate_strategy_params,
    _legacy_sell_id,
    apply_strategy_to_config,
    get_active_strategy_ids,
)
from app.strategies.registry import (
    _module_by_id,
    _sanitize_value,
    get_strategy,
    list_strategies,
)
from app.strategies.repository import (
    _save_strategy_store,
    _serialized_store_update,
    _strategy_store,
)
from app.strategies.validation import (
    normalize_strategy,
    validate_strategy,
)


def _system_strategy_with_submitted_params(raw_strategy: dict) -> Dict[str, Any]:
    sid = str((raw_strategy or {}).get("id") or "").strip()
    base = get_strategy(sid)
    if not base or base.get("origin") != "system":
        raise StrategyError("系统策略不存在")
    incoming_steps = raw_strategy.get("steps") if isinstance(raw_strategy, dict) else None
    if not isinstance(incoming_steps, list):
        raise StrategyError("系统策略参数缺少 steps")
    base_steps = base.get("steps") or []
    if len(incoming_steps) != len(base_steps):
        raise StrategyError("系统策略只允许修改参数，不能改变模块结构")
    out = copy.deepcopy(base)
    for idx, base_step in enumerate(base_steps):
        incoming = incoming_steps[idx] if isinstance(incoming_steps[idx], dict) else {}
        if incoming.get("module_id") != base_step.get("module_id"):
            raise StrategyError("系统策略只允许修改参数，不能改变模块顺序")
        if bool(incoming.get("enabled", True)) != bool(base_step.get("enabled", True)):
            raise StrategyError("系统策略只允许修改参数，不能改变模块启用状态")
        params = incoming.get("params") if isinstance(incoming.get("params"), dict) else {}
        out["steps"][idx]["params"] = _sanitize_value(params)
    return out


def _save_system_strategy_params(raw_strategy: dict) -> Dict[str, Any]:
    strategy = _system_strategy_with_submitted_params(raw_strategy)
    errors = validate_strategy(strategy, for_activation=False)
    if errors:
        raise StrategyError("；".join(errors))
    cfg = load_app_config_validated()
    old_strategies = copy.deepcopy(cfg.get("strategies") or {})
    old_sell_strategy = (cfg.get("pipeline") or {}).get("sell_strategy")
    updated = apply_strategy_to_config(cfg, strategy["strategy_type"], strategy_override=strategy)
    updated.pop("_strategy_runtime", None)
    updated["strategies"] = old_strategies
    if strategy["strategy_type"] == "sell":
        if old_sell_strategy is None:
            updated.setdefault("pipeline", {}).pop("sell_strategy", None)
        else:
            updated.setdefault("pipeline", {})["sell_strategy"] = old_sell_strategy
    save_app_config_validated(updated)
    fresh_cfg = load_app_config_validated()
    return _hydrate_strategy_params(get_strategy(strategy["id"]) or strategy, fresh_cfg)


@_serialized_store_update
def save_strategy(raw_strategy: dict) -> Dict[str, Any]:
    if str((raw_strategy or {}).get("id") or "").startswith("system."):
        return _save_system_strategy_params(raw_strategy)
    strategy = normalize_strategy(raw_strategy)
    errors = validate_strategy(strategy, for_activation=False)
    if errors:
        raise StrategyError("；".join(errors))
    store = _strategy_store()
    strategies = [s for s in store.get("strategies", []) if s.get("id") != strategy["id"]]
    strategies.append(strategy)
    store["strategies"] = strategies
    _save_strategy_store(store)
    return copy.deepcopy(strategy)


@_serialized_store_update
def delete_strategy(strategy_id: str) -> None:
    if strategy_id.startswith("system."):
        raise StrategyError("系统策略不可删除")
    store = _strategy_store()
    before = len(store.get("strategies", []))
    store["strategies"] = [s for s in store.get("strategies", []) if s.get("id") != strategy_id]
    if len(store["strategies"]) == before:
        raise StrategyError("策略不存在")
    cfg = load_app_config_validated()
    active = get_active_strategy_ids(cfg)
    scfg = dict(cfg.get("strategies") or {})
    changed = False
    if active.get("buy") == strategy_id:
        scfg["active_buy_strategy_id"] = "system.buy.default"
        changed = True
    if active.get("sell") == strategy_id:
        scfg["active_sell_strategy_id"] = _legacy_sell_id(cfg)
        changed = True
    _save_strategy_store(store)
    if changed:
        cfg["strategies"] = scfg
        save_app_config_validated(cfg)


@_serialized_store_update
def activate_strategy(strategy_id: str, *, risk_confirmed: bool) -> Dict[str, Any]:
    if not risk_confirmed:
        raise StrategyError("启用策略前必须确认风险")
    strategy = get_strategy(strategy_id)
    if not strategy:
        raise StrategyError("策略不存在")
    errors = validate_strategy(strategy, for_activation=True)
    if errors:
        raise StrategyError("；".join(errors))
    cfg = load_app_config_validated()
    scfg = dict(cfg.get("strategies") or {})
    if strategy["strategy_type"] == "buy":
        scfg["active_buy_strategy_id"] = strategy_id
    else:
        scfg["active_sell_strategy_id"] = strategy_id
        legacy = SELL_SYSTEM_TO_LEGACY.get(strategy_id)
        if legacy is not None:
            cfg.setdefault("pipeline", {})["sell_strategy"] = legacy
    cfg["strategies"] = scfg
    save_app_config_validated(cfg)
    return {"ok": True, "active": get_active_strategy_ids(cfg)}


def import_strategy(raw_strategy: dict) -> Dict[str, Any]:
    strategy = normalize_strategy(raw_strategy, imported=True)
    errors = validate_strategy(strategy, for_activation=False)
    if errors:
        raise StrategyError("；".join(errors))
    return save_strategy(strategy)


def get_strategy_payload() -> Dict[str, Any]:
    cfg = load_app_config_validated()
    active = get_active_strategy_ids(cfg)
    modules = []
    for mid, mod in _module_by_id().items():
        item = copy.deepcopy(mod)
        item.setdefault("builtin", mid in BUILTIN_MODULES)
        item.setdefault("origin", "builtin" if mid in BUILTIN_MODULES else "user")
        item.setdefault("enabled", mid in BUILTIN_MODULES)
        modules.append(item)
    return {
        "strategies": [_hydrate_strategy_params(s, cfg) for s in list_strategies()],
        "modules": sorted(
            modules,
            key=lambda m: (m.get("origin") != "builtin", m.get("category", ""), m.get("id", "")),
        ),
        "active": active,
        "limits": copy.deepcopy(STRATEGY_STEP_LIMITS),
        "legacy_sell_strategy": int((cfg.get("pipeline") or {}).get("sell_strategy", 4) or 4),
        "available_data": _available_data_catalog(),
    }
