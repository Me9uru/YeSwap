"""Strategy repository responsibility."""

import threading
from functools import wraps
from utils.json_store import atomic_write_json
import copy
import json
from pathlib import Path
from config import get_app_config_path


STRATEGY_STORE_PATH = get_app_config_path().parent / "strategies.json"


USER_MODULE_STORE_PATH = get_app_config_path().parent / "strategy_modules.json"


def _read_json(path: Path, fallback: dict) -> dict:
    if not path.exists():
        return copy.deepcopy(fallback)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else copy.deepcopy(fallback)
    except Exception:
        return copy.deepcopy(fallback)


_store_lock = threading.RLock()


def _serialized_store_update(fn):
    @wraps(fn)
    def update(*args, **kwargs):
        with _store_lock:
            return fn(*args, **kwargs)

    return update


def _write_json(path: Path, data: dict) -> None:
    atomic_write_json(path, data)


def _strategy_store() -> dict:
    data = _read_json(STRATEGY_STORE_PATH, {"strategies": []})
    data["strategies"] = [s for s in data.get("strategies", []) if isinstance(s, dict)]
    return data


def _save_strategy_store(data: dict) -> None:
    _write_json(STRATEGY_STORE_PATH, {"strategies": data.get("strategies", [])})


def _module_store() -> dict:
    data = _read_json(USER_MODULE_STORE_PATH, {"modules": []})
    data["modules"] = [m for m in data.get("modules", []) if isinstance(m, dict)]
    return data


def _save_module_store(data: dict) -> None:
    _write_json(USER_MODULE_STORE_PATH, {"modules": data.get("modules", [])})
