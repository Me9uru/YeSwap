"""Steam Guard code generation."""

import base64
import hashlib
import hmac
import re
import struct
import time
from typing import Optional
from app.configuration.repository import load_app_config_validated


def _normalize_secret(raw: str) -> str:
    return re.sub(r"\\u([0-9a-fA-F]{4})", lambda m: chr(int(m.group(1), 16)), raw)


def _generate_steam_guard_code(shared_secret: str) -> Optional[str]:
    if not shared_secret:
        return None
    secret_str = _normalize_secret(shared_secret.strip())
    try:
        secret_bytes = base64.b64decode(secret_str)
    except Exception:
        return None
    ts = int(time.time())
    time_buffer = struct.pack(">Q", ts // 30)
    hmac_hash = hmac.new(secret_bytes, time_buffer, hashlib.sha1).digest()
    offset = hmac_hash[19] & 0xF
    code_int = struct.unpack(">I", hmac_hash[offset : offset + 4])[0] & 0x7FFFFFFF
    chars = "23456789BCDFGHJKMNPQRTVWXY"
    out = []
    for _ in range(5):
        out.append(chars[code_int % 26])
        code_int //= 26
    return "".join(out)


def api_steam_guard():
    cfg = load_app_config_validated()
    sg = cfg.get("steam_guard") or {}
    shared_secret = (sg.get("shared_secret") or "").strip()
    if not shared_secret:
        return {"ok": False, "error": "未配置 shared_secret"}
    code = _generate_steam_guard_code(shared_secret)
    if not code:
        return {"ok": False, "error": "shared_secret 无效"}
    now_ts = int(time.time())
    return {"ok": True, "code": code, "server_time": now_ts, "period": 30}
