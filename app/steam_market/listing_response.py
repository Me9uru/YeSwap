"""Steam 上架响应判定与敏感信息遮盖。"""

import re
from typing import Optional

from steam.market import parse_sell_response


def _listing_response_body_preview(value: object, limit: int = 160) -> str:
    """Return a compact, bounded response excerpt suitable for logs."""
    if value is None:
        return "<empty>"
    if isinstance(value, bytes):
        text = value.decode("utf-8", errors="replace")
    else:
        text = str(value)
    source_was_truncated = len(text) > max(limit * 4, 640)
    if source_was_truncated:
        text = text[: max(limit * 4, 640)]
    text = " ".join(text.split())
    if not text:
        return "<empty>"
    text = re.sub(
        r"(?i)\b(bearer)\s+[a-z0-9._~+/=-]+",
        r"\1 ***",
        text,
    )
    text = re.sub(
        (
            r"(?i)\b("
            r"steamloginsecure|sessionid|access[_-]?token|"
            r"refresh[_-]?token|token|cookie"
            r")(\s*[\"']?\s*[:=]\s*[\"']?)([^\"'\s,;&}<]+)"
        ),
        r"\1\2***",
        text,
    )
    if source_was_truncated or len(text) > limit:
        return text[: limit - 3] + "..."
    return text


def _parse_listing_response(out: object) -> tuple[Optional[dict], str]:
    """Validate a low-level listing response and return diagnostics on failure."""
    if out is None:
        return None, "无响应"
    if not isinstance(out, dict):
        return None, f"返回类型异常: {type(out).__name__}"

    status_code = out.get("status_code")
    status_label = str(status_code) if status_code is not None else "unknown"
    raw_text = out.get("text")
    _, data = parse_sell_response(raw_text)
    if data is None:
        return (
            None,
            f"HTTP {status_label}; body={_listing_response_body_preview(raw_text)}",
        )
    return data, ""


def _listing_response_message(data: dict, limit: int = 80) -> str:
    value = data.get("message")
    if value in (None, ""):
        return ""
    return _listing_response_body_preview(value, limit=limit)


def _listing_response_is_success(out: object, data: dict, message: str) -> bool:
    """Accept success only from the expected Steam HTTP response contract."""
    if not isinstance(out, dict) or out.get("status_code") != 200:
        return False
    message_lower = message.lower()
    return (
        data.get("success") is True
        or "pending confirmation" in message_lower
        or "already have a listing" in message_lower
    )
