"""Strategy validation responsibility."""

import re
import time
from typing import Any, Dict, List
from utils.validation import coerce_bool as _coerce_bool
from app.strategies.catalog import (
    BUY_REQUIRED_MODULES,
    SELL_LIST_REQUIRED_MODULES,
    SELL_PRICING_CORE_MODULES,
    STRATEGY_STEP_LIMITS,
    STRATEGY_TYPES,
    StrategyError,
    USER_MODULE_EFFECTS,
)
from app.strategies.registry import (
    _is_declarative_user_module,
    _module_by_id,
    _normalize_max_instances,
    _sanitize_value,
)


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value.strip().lower()).strip("-")
    return slug or str(int(time.time()))


def _custom_id(strategy_type: str, name: str) -> str:
    return f"custom.{strategy_type}.{_slugify(name)}.{int(time.time())}"


def _normalize_step(raw: dict) -> dict:
    module_id = str(raw.get("module_id") or raw.get("module") or "").strip()
    return {
        "module_id": module_id,
        "enabled": _coerce_bool(raw.get("enabled"), True),
        "params": _sanitize_value(raw.get("params") if isinstance(raw.get("params"), dict) else {}),
    }


def normalize_strategy(raw: dict, *, imported: bool = False) -> Dict[str, Any]:
    data = _sanitize_value(raw or {})
    strategy_type = str(data.get("strategy_type") or data.get("type") or "").strip()
    if strategy_type not in STRATEGY_TYPES:
        raise StrategyError("strategy_type 必须是 buy 或 sell")
    name = str(data.get("name") or "未命名策略").strip() or "未命名策略"
    sid = str(data.get("id") or "").strip()
    if not sid or sid.startswith("system."):
        sid = _custom_id(strategy_type, name)
    if not sid.startswith("custom."):
        sid = f"custom.{strategy_type}.{_slugify(sid)}"
    steps = [_normalize_step(s) for s in data.get("steps", []) if isinstance(s, dict)]
    if not steps:
        raise StrategyError("策略至少需要一个模块")
    return {
        "id": sid,
        "name": name,
        "strategy_type": strategy_type,
        "origin": "custom",
        "readonly": False,
        "description": str(data.get("description") or "").strip(),
        "steps": steps,
        "created_at": data.get("created_at") or time.time(),
        "updated_at": time.time(),
        "imported": bool(imported or data.get("imported")),
    }


def _validate_step_params(module_id: str, mod: dict, params: dict) -> List[str]:
    errors: List[str] = []
    schema = mod.get("params_schema") if isinstance(mod.get("params_schema"), dict) else {}
    if not isinstance(params, dict):
        return [f"Module {module_id} params must be an object"]
    for key, meta in schema.items():
        if not isinstance(meta, dict) or key not in params:
            continue
        value = params.get(key)
        label = meta.get("label") or key
        expected = str(meta.get("type") or "string").lower()
        if value is None or value == "":
            continue
        try:
            if expected == "integer":
                if isinstance(value, bool):
                    raise ValueError
                numeric = int(value)
                if float(value) != numeric:
                    raise ValueError
                if "min" in meta and numeric < int(meta["min"]):
                    errors.append(f"{label} must be >= {meta['min']}")
                if "max" in meta and numeric > int(meta["max"]):
                    errors.append(f"{label} must be <= {meta['max']}")
            elif expected == "number":
                if isinstance(value, bool):
                    raise ValueError
                numeric = float(value)
                if "min" in meta and numeric < float(meta["min"]):
                    errors.append(f"{label} must be >= {meta['min']}")
                if "max" in meta and numeric > float(meta["max"]):
                    errors.append(f"{label} must be <= {meta['max']}")
            elif expected == "boolean":
                if not isinstance(value, bool):
                    errors.append(f"{label} must be boolean")
            elif expected == "array":
                if not isinstance(value, list):
                    errors.append(f"{label} must be an array")
            elif expected == "select":
                options = meta.get("options") if isinstance(meta.get("options"), list) else []
                allowed = [o.get("value") if isinstance(o, dict) else o for o in options]
                if allowed and value not in allowed:
                    errors.append(f"{label} must be one of: {', '.join(map(str, allowed))}")
        except (TypeError, ValueError):
            errors.append(f"{label} has invalid value")
    return errors


def _validate_user_module_contract(module_id: str, mod: dict, *, for_activation: bool) -> List[str]:
    errors: List[str] = []
    if (mod or {}).get("origin") != "user":
        return errors
    if not for_activation:
        return errors
    if not _is_declarative_user_module(mod):
        errors.append(
            f"User module {module_id} is registered but cannot be activated because it is not a safe declarative module"
        )
        return errors
    if mod.get("enabled") is False:
        errors.append(f"User module {module_id} is disabled")
    if not (mod.get("stages") or []):
        errors.append(f"User module {module_id} has no supported runtime stage")
    if mod.get("effect") not in USER_MODULE_EFFECTS:
        errors.append(f"User module {module_id} has unsupported effect")
    if mod.get("effect") in {"guard", "filter"} and not (mod.get("conditions") or []):
        errors.append(f"User module {module_id} needs at least one condition")
    return errors


def validate_strategy(strategy: dict, *, for_activation: bool = False) -> List[str]:
    errors: List[str] = []
    stype = strategy.get("strategy_type")
    if stype not in STRATEGY_TYPES:
        errors.append("strategy_type 必须是 buy 或 sell")
        return errors
    modules = _module_by_id()
    steps = strategy.get("steps") or []
    step_limit = STRATEGY_STEP_LIMITS.get(stype, 16)
    if len(steps) > step_limit:
        errors.append(f"{'购入' if stype == 'buy' else '出售'}策略最多添加 {step_limit} 个模块")
    enabled_ids = set()
    enabled_indices: Dict[str, int] = {}
    all_counts: Dict[str, int] = {}
    for idx, step in enumerate(steps):
        module_id = step.get("module_id")
        if not module_id:
            errors.append(f"第 {idx + 1} 步缺少 module_id")
            continue
        all_counts[module_id] = all_counts.get(module_id, 0) + 1
        mod = modules.get(module_id)
        if not mod:
            errors.append(f"未知模块: {module_id}")
            continue
        if stype not in (mod.get("strategy_types") or []):
            errors.append(f"模块 {module_id} 不能用于 {stype} 策略")
        errors.extend(
            _validate_step_params(
                module_id, mod, step.get("params") if isinstance(step.get("params"), dict) else {}
            )
        )
        if step.get("enabled") is not False:
            enabled_ids.add(module_id)
            enabled_indices.setdefault(module_id, idx)
            if for_activation and (mod.get("origin") == "user" or mod.get("builtin") is False):
                errors.append(f"用户模块 {module_id} 首版只允许登记，不能启用")
            if for_activation and mod.get("enabled") is False and mod.get("origin") == "user":
                errors.append(f"用户模块 {module_id} 当前未启用")
    if for_activation:
        declarative_user_ids = set()
        for module_id in sorted(enabled_ids):
            mod = modules.get(module_id) or {}
            if mod.get("origin") == "user":
                errors.extend(_validate_user_module_contract(module_id, mod, for_activation=True))
                if _is_declarative_user_module(mod):
                    declarative_user_ids.add(module_id)
        if declarative_user_ids:
            errors = [
                e
                for e in errors
                if not any(
                    module_id in e and not e.startswith("User module ")
                    for module_id in declarative_user_ids
                )
            ]
    for module_id, count in sorted(all_counts.items()):
        mod = modules.get(module_id)
        if not mod:
            continue
        max_instances = _normalize_max_instances(mod.get("max_instances"))
        if count > max_instances:
            label = mod.get("name") or module_id
            errors.append(f"模块 {label} 最多只能添加 {max_instances} 次")
    conflict_pairs = set()
    for module_id in sorted(enabled_ids):
        mod = modules.get(module_id) or {}
        label = mod.get("name") or module_id
        for other_id in mod.get("conflicts") or []:
            if other_id in enabled_ids:
                pair = tuple(sorted((module_id, other_id)))
                if pair in conflict_pairs:
                    continue
                conflict_pairs.add(pair)
                other_label = (modules.get(other_id) or {}).get("name") or other_id
                errors.append(f"模块 {label} 与 {other_label} 互斥，不能同时启用")
        for req_id in mod.get("requires") or []:
            if req_id not in enabled_ids:
                req_label = (modules.get(req_id) or {}).get("name") or req_id
                errors.append(f"模块 {label} 需要同时启用 {req_label}")
        for req_id in mod.get("requires") or []:
            if req_id in enabled_ids and enabled_indices.get(req_id, 0) > enabled_indices.get(
                module_id, 0
            ):
                req_label = (modules.get(req_id) or {}).get("name") or req_id
                errors.append(f"Module {label} must run after {req_label}")
        for group in mod.get("requires_any") or []:
            if group and not any(req_id in enabled_ids for req_id in group):
                if module_id == "action.steam_list" and set(group) == set(
                    SELL_PRICING_CORE_MODULES
                ):
                    continue
                req_labels = [(modules.get(req_id) or {}).get("name") or req_id for req_id in group]
                errors.append(f"模块 {label} 需要至少启用一个依赖模块：{' / '.join(req_labels)}")
        for group in mod.get("requires_any") or []:
            if (
                group
                and any(req_id in enabled_ids for req_id in group)
                and not any(
                    req_id in enabled_ids
                    and enabled_indices.get(req_id, 0) < enabled_indices.get(module_id, 0)
                    for req_id in group
                )
            ):
                req_labels = [(modules.get(req_id) or {}).get("name") or req_id for req_id in group]
                errors.append(f"Module {label} must run after one of: {' / '.join(req_labels)}")
    if stype == "buy":
        for module_id in BUY_REQUIRED_MODULES:
            if module_id not in enabled_ids:
                label = (modules.get(module_id) or {}).get("name") or module_id
                errors.append(f"购入策略必须包含并启用 {label}")
    if stype == "sell":
        has_pause = "action.pause_auto_sell" in enabled_ids
        has_list = "action.steam_list" in enabled_ids
        if not has_pause and not has_list:
            errors.append("出售策略必须包含 Steam 上架或暂停自动出售动作")
        if has_list:
            for module_id in SELL_LIST_REQUIRED_MODULES:
                if module_id not in enabled_ids:
                    label = (modules.get(module_id) or {}).get("name") or module_id
                    errors.append(f"出售上架策略必须包含并启用 {label}")
            if not any(module_id in enabled_ids for module_id in SELL_PRICING_CORE_MODULES):
                labels = [
                    (modules.get(module_id) or {}).get("name") or module_id
                    for module_id in SELL_PRICING_CORE_MODULES
                ]
                errors.append(f"出售上架策略必须包含并启用一个定价模块：{' / '.join(labels)}")
    return errors
