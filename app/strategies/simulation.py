"""Strategy simulation responsibility."""

from app.strategies.rules import discount_is_acceptable
import copy
from typing import Any, Dict
from app.strategies.module_specs import BUILTIN_MODULES
from app.strategies.catalog import (
    MODULE_SAMPLE_OUTPUTS,
)
from app.strategies.expressions import (
    _available_data_catalog,
    _evaluate_declarative_module,
)
from app.strategies.registry import (
    _is_declarative_user_module,
    _module_by_id,
    get_strategy,
)
from app.strategies.validation import (
    normalize_strategy,
    validate_strategy,
)


def _sample_strategy_context(strategy_type: str) -> Dict[str, Any]:
    if strategy_type == "sell":
        return {
            "item": {"name": "Sample Item", "assetid": "123", "market_hash_name": "Sample Item"},
            "buy_record": {"price": 8.0, "market_price": 12.0},
            "listing": {"list_price": 12.8, "display_price": 12.8},
            "config": {"pipeline": {}, "stability": {}},
        }
    return {
        "item": {
            "name": "Sample Item",
            "goods_id": 1001,
            "min_price": 9.8,
            "daily_volume": 80,
            "ratio": 0.82,
            "steam_market_name": "Sample Item",
        },
        "config": {"pipeline": {}, "stability": {}},
    }


def _simulate_builtin_step(module_id: str, step: dict, outputs: dict) -> Dict[str, Any]:
    params = {
        key: copy.deepcopy(meta.get("default"))
        for key, meta in BUILTIN_MODULES[module_id].get("params_schema", {}).items()
    }
    params.update(step.get("params") or {})
    output = copy.deepcopy(MODULE_SAMPLE_OUTPUTS.get(module_id) or {})
    if module_id == "action.pause_auto_sell":
        status, reason = "wait", "样例模拟：自动出售在此暂停"
    elif module_id.startswith("action."):
        status, reason = "action", "模拟跳过真实交易 / Simulation skipped the real trading action"
    elif module_id == "guard.max_discount":
        ratio = float(
            (outputs.get("buy.steam_sell_depth") or {}).get(
                "estimated_ratio", output["estimated_ratio"]
            )
        )
        limit = float(params["max_discount"])
        output = {"estimated_ratio": ratio, "limit": limit}
        status = "pass" if discount_is_acceptable(ratio, limit) else "reject"
        reason = f"样例行情：折扣 {ratio:.4f} 必须低于 {limit:.4f}"
    else:
        status = "sample"
        reason = "仅展示样例输出；此步骤尚未执行真实规则，不能据此判断通过"
    if output:
        outputs[module_id] = output
    return {"status": status, "reason": reason, "output": output}


def simulate_strategy(raw: dict) -> Dict[str, Any]:
    strategy = get_strategy(raw.get("strategy_id") or "") if raw.get("strategy_id") else None
    if strategy is None:
        strategy = normalize_strategy(raw.get("strategy") or raw)
    errors = validate_strategy(strategy, for_activation=False)
    if errors:
        return {"ok": False, "errors": errors, "results": []}
    modules = _module_by_id()
    context = _sample_strategy_context(strategy.get("strategy_type"))
    outputs: Dict[str, Any] = {}
    results = []
    for idx, step in enumerate(strategy.get("steps") or [], start=1):
        module_id = step.get("module_id")
        mod = modules.get(module_id)
        output: Dict[str, Any] = {}
        if step.get("enabled") is False:
            status = "skip"
            reason = "Module is disabled"
        elif not mod:
            status = "error"
            reason = "Unknown module"
        elif mod.get("origin") == "user":
            if _is_declarative_user_module(mod):
                evaluated = _evaluate_declarative_module(
                    mod,
                    step.get("params") if isinstance(step.get("params"), dict) else {},
                    context=context,
                    outputs=outputs,
                )
                status = evaluated["status"]
                reason = evaluated["reason"]
                output = evaluated.get("output") or {}
                outputs[module_id] = copy.deepcopy(output)
            else:
                status = "error"
                reason = "User code modules are registered only and are not executed"
        else:
            evaluated = _simulate_builtin_step(module_id, step, outputs)
            status = evaluated["status"]
            reason = evaluated["reason"]
            output = evaluated.get("output") or {}
        results.append(
            {
                "index": idx,
                "module_id": module_id,
                "module_name": (mod or {}).get("name", module_id),
                "status": status,
                "reason": reason,
                "output_keys": sorted(output.keys()) if isinstance(output, dict) else [],
                "output": output,
            }
        )
    return {
        "ok": True,
        "mode": "sample",
        "strategy": strategy,
        "results": results,
        "available_data": _available_data_catalog(),
    }
