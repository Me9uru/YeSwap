"""Strategy expressions responsibility."""

import copy
from typing import Any, Dict, List, Optional
from app.strategies.catalog import (
    CONDITION_OPERATORS,
    MODULE_DATA_OUTPUTS,
    STRATEGY_CONTEXT_FIELDS,
    USER_MODULE_FAIL_STATUSES,
    USER_MODULE_STAGES,
)
from app.strategies.registry import (
    _clean_logic,
    _is_declarative_user_module,
    _module_by_id,
)


_MISSING = object()


def _available_data_catalog() -> Dict[str, Any]:
    return {
        "context_fields": copy.deepcopy(STRATEGY_CONTEXT_FIELDS),
        "module_outputs": copy.deepcopy(MODULE_DATA_OUTPUTS),
        "operators": sorted(CONDITION_OPERATORS),
        "stages": {k: sorted(v) for k, v in USER_MODULE_STAGES.items()},
    }


def _get_nested(value: Any, parts: List[str]) -> Any:
    current = value
    for part in parts:
        if current is _MISSING:
            return _MISSING
        if isinstance(current, dict):
            if part not in current:
                return _MISSING
            current = current.get(part)
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (TypeError, ValueError, IndexError):
                return _MISSING
        else:
            return _MISSING
    return current


def _resolve_output_path(outputs: dict, path: str) -> Any:
    remainder = path[len("outputs.") :]
    matches = [
        module_id
        for module_id in (outputs or {}).keys()
        if remainder == module_id or remainder.startswith(f"{module_id}.")
    ]
    if not matches:
        return _MISSING
    module_id = max(matches, key=len)
    rest = remainder[len(module_id) :].lstrip(".")
    return _get_nested(outputs.get(module_id), rest.split(".") if rest else [])


def _resolve_path(path: Any, data: dict) -> Any:
    if not isinstance(path, str):
        return _MISSING
    path = path.strip()
    if not path:
        return _MISSING
    if path.startswith("outputs."):
        return _resolve_output_path(data.get("outputs") or {}, path)
    parts = path.split(".")
    root = parts[0]
    if root not in data:
        return _MISSING
    return _get_nested(data.get(root), parts[1:])


def _as_number(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is _MISSING or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _compare_values(left: Any, op: str, right: Any = _MISSING) -> bool:
    if op == "exists":
        return left is not _MISSING and left is not None
    if op == "missing":
        return left is _MISSING or left is None
    if left is _MISSING:
        return False
    if op in {"gt", "gte", "lt", "lte", "between"}:
        left_num = _as_number(left)
        if left_num is None:
            return False
        if op == "between":
            if not isinstance(right, list) or len(right) != 2:
                return False
            low = _as_number(right[0])
            high = _as_number(right[1])
            return low is not None and high is not None and low <= left_num <= high
        right_num = _as_number(right)
        if right_num is None:
            return False
        if op == "gt":
            return left_num > right_num
        if op == "gte":
            return left_num >= right_num
        if op == "lt":
            return left_num < right_num
        return left_num <= right_num
    if op == "eq":
        return left == right
    if op == "ne":
        return left != right
    if op == "contains":
        return (
            str(right) in str(left) if not isinstance(left, (list, tuple, set)) else right in left
        )
    if op == "not_contains":
        return not _compare_values(left, "contains", right)
    if op == "in":
        return isinstance(right, (list, tuple, set)) and left in right
    if op == "not_in":
        return not _compare_values(left, "in", right)
    return False


def _evaluate_condition(condition: dict, data: dict) -> Dict[str, Any]:
    left_path = condition.get("left")
    op = str(condition.get("op") or "eq").lower()
    left_value = _resolve_path(left_path, data)
    if "right_path" in condition:
        right_value = _resolve_path(condition.get("right_path"), data)
        right_repr = condition.get("right_path")
    else:
        right_value = condition.get("value", _MISSING)
        right_repr = right_value
    ok = _compare_values(left_value, op, right_value)
    return {
        "left": left_path,
        "op": op,
        "right": None if right_value is _MISSING else right_repr,
        "left_value": None if left_value is _MISSING else left_value,
        "passed": bool(ok),
        "label": condition.get("label") or "",
    }


def _evaluate_declarative_module(
    mod: dict, params: dict, context: Optional[dict] = None, outputs: Optional[dict] = None
) -> Dict[str, Any]:
    data = copy.deepcopy(context or {})
    data.setdefault("params", params or {})
    data.setdefault("outputs", outputs or {})
    conditions = mod.get("conditions") or []
    checks = [_evaluate_condition(cond, data) for cond in conditions]
    logic = _clean_logic(mod.get("logic"))
    passed = (
        any(c["passed"] for c in checks) if logic == "any" else all(c["passed"] for c in checks)
    )
    if not checks:
        passed = True
    status = "pass" if passed else str(mod.get("fail_status") or "reject").lower()
    if status not in USER_MODULE_FAIL_STATUSES:
        status = "reject"
    message = str(mod.get("message") or "").strip()
    if not message:
        message = (
            "Declarative conditions passed" if passed else "Declarative conditions did not pass"
        )
    return {
        "status": status,
        "reason": message,
        "output": {
            "logic": logic,
            "passed": passed,
            "passed_conditions": sum(1 for c in checks if c["passed"]),
            "total_conditions": len(checks),
            "conditions": checks,
        },
    }


def _module_stage_matches(mod: dict, strategy_type: str, stage: str) -> bool:
    stages = mod.get("stages")
    if not stages:
        stages = sorted(USER_MODULE_STAGES.get(strategy_type) or [])
    return "*" in stages or stage in stages


def evaluate_strategy_runtime_modules(
    config: dict,
    strategy_type: str,
    stage: str,
    *,
    context: Optional[dict] = None,
    outputs: Optional[dict] = None,
) -> tuple:
    runtime = ((config or {}).get("_strategy_runtime") or {}).get(strategy_type) or {}
    steps = runtime.get("steps") or []
    modules = runtime.get("modules") or _module_by_id()
    results = []
    for idx, step in enumerate(steps, start=1):
        if step.get("enabled") is False:
            continue
        module_id = step.get("module_id")
        mod = modules.get(module_id) or {}
        if not _is_declarative_user_module(mod):
            continue
        if not _module_stage_matches(mod, strategy_type, stage):
            continue
        result = _evaluate_declarative_module(
            mod,
            step.get("params") if isinstance(step.get("params"), dict) else {},
            context=context,
            outputs=outputs,
        )
        result.update(
            {
                "index": idx,
                "module_id": module_id,
                "module_name": mod.get("name") or module_id,
                "stage": stage,
            }
        )
        results.append(result)
        if result.get("status") in {"reject", "wait", "error"}:
            return results, result
    return results, None
