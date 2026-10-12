"""Strategy registry responsibility."""

import copy
import time
from typing import Any, Dict, List, Optional
from app.strategies.module_specs import BUILTIN_MODULES
from app.strategies.system_presets import SYSTEM_STRATEGIES
from app.strategies.catalog import (
    CONDITION_OPERATORS,
    DECLARATIVE_MODULE_KINDS,
    DEFAULT_MODULE_MAX_INSTANCES,
    MODULE_CONSTRAINTS,
    MODULE_DATA_OUTPUTS,
    MODULE_SAMPLE_OUTPUTS,
    SENSITIVE_KEY_RE,
    STRATEGY_TYPES,
    StrategyError,
    USER_MODULE_CODE_KEYS,
    USER_MODULE_EFFECTS,
    USER_MODULE_FAIL_STATUSES,
    USER_MODULE_STAGES,
)
from app.strategies.repository import (
    _module_store,
    _save_module_store,
    _serialized_store_update,
    _strategy_store,
)


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, dict):
        clean = {}
        for k, v in value.items():
            if SENSITIVE_KEY_RE.search(str(k)):
                continue
            clean[k] = _sanitize_value(v)
        return clean
    if isinstance(value, list):
        return [_sanitize_value(v) for v in value]
    return value


def _system_by_id() -> Dict[str, Dict[str, Any]]:
    return {s["id"]: copy.deepcopy(s) for s in SYSTEM_STRATEGIES}


def _user_module_by_id() -> Dict[str, Dict[str, Any]]:
    out = {}
    for m in _module_store().get("modules", []):
        mid = str(m.get("id") or "").strip()
        if mid:
            out[mid] = copy.deepcopy(m)
    return out


def _clean_module_id_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()]


def _clean_requires_any(value: Any) -> List[List[str]]:
    if not isinstance(value, list):
        return []
    groups: List[List[str]] = []
    for group in value:
        cleaned = _clean_module_id_list(group)
        if cleaned:
            groups.append(cleaned)
    return groups


def _clean_logic(value: Any) -> str:
    logic = str(value or "all").strip().lower()
    return "any" if logic == "any" else "all"


def _clean_stage_list(strategy_types: List[str], stage: Any, stages: Any) -> List[str]:
    raw = []
    if isinstance(stages, list):
        raw.extend(str(v).strip() for v in stages if str(v).strip())
    if str(stage or "").strip():
        raw.append(str(stage).strip())
    if not raw:
        for stype in strategy_types:
            raw.extend(sorted(USER_MODULE_STAGES.get(stype) or []))
    allowed = set()
    for stype in strategy_types:
        allowed.update(USER_MODULE_STAGES.get(stype) or set())
    return sorted({s for s in raw if s in allowed})


def _clean_conditions(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        left = str(item.get("left") or item.get("path") or "").strip()
        op = str(item.get("op") or item.get("operator") or "eq").strip().lower()
        if not left or op not in CONDITION_OPERATORS:
            continue
        cleaned: Dict[str, Any] = {
            "left": left,
            "op": op,
            "label": str(item.get("label") or "").strip(),
        }
        if "right_path" in item:
            cleaned["right_path"] = str(item.get("right_path") or "").strip()
        elif item.get("right_is_path"):
            cleaned["right_path"] = str(item.get("right") or item.get("value") or "").strip()
        elif "value" in item:
            cleaned["value"] = _sanitize_value(item.get("value"))
        elif "right" in item:
            cleaned["value"] = _sanitize_value(item.get("right"))
        out.append(cleaned)
    return out


def _has_user_code_fields(data: dict) -> bool:
    return any(key in data for key in USER_MODULE_CODE_KEYS)


def _is_declarative_user_module(mod: dict) -> bool:
    if (mod or {}).get("origin") != "user":
        return False
    kind = str(mod.get("module_kind") or mod.get("kind") or "").strip().lower()
    if kind not in DECLARATIVE_MODULE_KINDS:
        return False
    return not _has_user_code_fields(mod)


def _normalize_user_module_kind(data: dict) -> str:
    raw = str(data.get("module_kind") or data.get("kind") or "").strip().lower()
    if raw in DECLARATIVE_MODULE_KINDS and not _has_user_code_fields(data):
        return "declarative"
    return "external_code" if _has_user_code_fields(data) else "manifest"


def _normalize_max_instances(value: Any) -> int:
    try:
        return max(1, int(value or DEFAULT_MODULE_MAX_INSTANCES))
    except (TypeError, ValueError):
        return DEFAULT_MODULE_MAX_INSTANCES


def _apply_module_defaults(module_id: str, mod: Dict[str, Any]) -> Dict[str, Any]:
    constraints = MODULE_CONSTRAINTS.get(module_id) or {}
    for key, value in constraints.items():
        mod[key] = copy.deepcopy(value)
    mod.setdefault("data_outputs", copy.deepcopy(MODULE_DATA_OUTPUTS.get(module_id) or {}))
    mod.setdefault("sample_output", copy.deepcopy(MODULE_SAMPLE_OUTPUTS.get(module_id) or {}))
    mod["max_instances"] = _normalize_max_instances(mod.get("max_instances"))
    mod["conflicts"] = _clean_module_id_list(mod.get("conflicts"))
    uses_modules = _clean_module_id_list(mod.get("uses_modules"))
    mod["uses_modules"] = uses_modules
    mod["requires"] = sorted(set(_clean_module_id_list(mod.get("requires")) + uses_modules))
    mod["requires_any"] = _clean_requires_any(mod.get("requires_any"))
    if mod.get("origin") == "user":
        strategy_types = [t for t in (mod.get("strategy_types") or []) if t in STRATEGY_TYPES]
        mod["stages"] = _clean_stage_list(strategy_types, mod.get("stage"), mod.get("stages"))
        mod["effect"] = str(mod.get("effect") or "guard").strip().lower()
        if mod["effect"] not in USER_MODULE_EFFECTS:
            mod["effect"] = "guard"
        mod["logic"] = _clean_logic(mod.get("logic"))
        mod["conditions"] = _clean_conditions(mod.get("conditions"))
        fail_status = str(mod.get("fail_status") or "reject").strip().lower()
        mod["fail_status"] = fail_status if fail_status in USER_MODULE_FAIL_STATUSES else "reject"
    return mod


def _module_by_id() -> Dict[str, Dict[str, Any]]:
    out = {}
    for mid, mod in BUILTIN_MODULES.items():
        item = copy.deepcopy(mod)
        item.setdefault("origin", "builtin")
        item.setdefault("builtin", True)
        item.setdefault("enabled", True)
        out[mid] = _apply_module_defaults(mid, item)
    for mid, mod in _user_module_by_id().items():
        mod.setdefault("origin", "user")
        mod.setdefault("enabled", False)
        mod.setdefault("executable", False)
        mod.setdefault("builtin", False)
        out[mid] = _apply_module_defaults(mid, mod)
    return out


def list_custom_strategies() -> List[Dict[str, Any]]:
    return [copy.deepcopy(s) for s in _strategy_store().get("strategies", [])]


def list_strategies() -> List[Dict[str, Any]]:
    return [copy.deepcopy(s) for s in SYSTEM_STRATEGIES] + list_custom_strategies()


def get_strategy(strategy_id: str) -> Optional[Dict[str, Any]]:
    for s in list_strategies():
        if s.get("id") == strategy_id:
            return s
    return None


@_serialized_store_update
def import_user_module(raw_manifest: dict) -> Dict[str, Any]:
    data = _sanitize_value(raw_manifest or {})
    mid = str(data.get("id") or "").strip()
    if not mid:
        raise StrategyError("Module manifest is missing id")
    if mid in BUILTIN_MODULES:
        raise StrategyError("Cannot override builtin modules")
    strategy_types = data.get("strategy_types")
    if not isinstance(strategy_types, list):
        mtype = data.get("strategy_type") or data.get("type")
        strategy_types = [mtype] if mtype else []
    strategy_types = [t for t in strategy_types if t in STRATEGY_TYPES]
    if not strategy_types:
        raise StrategyError("Module must declare strategy_types: buy or sell")

    module_kind = _normalize_user_module_kind(data)
    effect = str(data.get("effect") or "guard").strip().lower()
    if effect not in USER_MODULE_EFFECTS:
        effect = "guard"
    fail_status = str(data.get("fail_status") or "reject").strip().lower()
    if fail_status not in USER_MODULE_FAIL_STATUSES:
        fail_status = "reject"
    uses_modules = _clean_module_id_list(data.get("uses_modules"))
    requires = sorted(set(_clean_module_id_list(data.get("requires")) + uses_modules))
    stages = _clean_stage_list(strategy_types, data.get("stage"), data.get("stages"))
    conditions = _clean_conditions(data.get("conditions"))
    if module_kind == "declarative":
        if effect in {"guard", "filter"} and not conditions:
            raise StrategyError("Declarative guard/filter modules need at least one condition")
        if not stages:
            raise StrategyError("Declarative module stage is not supported for its strategy type")

    manifest = {
        "id": mid,
        "name": str(data.get("name") or mid),
        "category": str(data.get("category") or "custom"),
        "strategy_types": strategy_types,
        "description": str(data.get("description") or ""),
        "params_schema": data.get("params_schema")
        if isinstance(data.get("params_schema"), dict)
        else {},
        "max_instances": _normalize_max_instances(data.get("max_instances")),
        "conflicts": _clean_module_id_list(data.get("conflicts")),
        "requires": requires,
        "requires_any": _clean_requires_any(data.get("requires_any")),
        "uses_modules": uses_modules,
        "module_kind": module_kind,
        "effect": effect,
        "logic": _clean_logic(data.get("logic")),
        "conditions": conditions,
        "fail_status": fail_status,
        "message": str(data.get("message") or ""),
        "stages": stages,
        "capabilities": ["read_context", "read_module_outputs"]
        if module_kind == "declarative"
        else [],
        "origin": "user",
        "enabled": module_kind == "declarative",
        "executable": False,
        "imported_at": time.time(),
    }
    store = _module_store()
    store["modules"] = [m for m in store.get("modules", []) if m.get("id") != mid]
    store["modules"].append(manifest)
    _save_module_store(store)
    return copy.deepcopy(manifest)
