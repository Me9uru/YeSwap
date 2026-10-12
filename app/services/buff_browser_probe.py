"""BUFF 浏览器页面检查与会话验证请求脚本。"""

from typing import Any


_CHALLENGE_MARKERS = (
    "captcha",
    "geetest",
    "challenge-platform",
    "cf-chl-",
    "verify you are human",
    "访问验证",
    "安全验证",
    "请完成验证",
    "拖动滑块",
    "行为验证",
    "访问过于频繁",
    "请求过于频繁",
    "risk_control",
)
_VISIBLE_CHALLENGE_MARKERS = (
    "challenge-platform",
    "cf-chl-",
    "verify you are human",
    "访问验证",
    "安全验证",
    "请完成验证",
    "拖动滑块",
    "行为验证",
    "访问过于频繁",
    "请求过于频繁",
)
def read_buff_browser_user_agent(page: Any) -> str:
    """Read the exact UA exposed by the browser page, if available."""

    try:
        value = page.evaluate("() => navigator.userAgent")
    except Exception:
        return ""
    return str(value or "").strip()


def detect_buff_challenge(page: Any) -> str:
    """Return a human-readable reason when the loaded page is a risk challenge."""

    location_pieces = []
    try:
        location_pieces.append(str(getattr(page, "url", "") or ""))
    except Exception:
        pass
    try:
        location_pieces.append(str(page.title() or ""))
    except Exception:
        pass
    location = "\n".join(location_pieces).lower()
    marker = next((item for item in _CHALLENGE_MARKERS if item in location), "")
    if marker:
        return f"BUFF 页面要求安全验证（命中标记: {marker}）"

    body_text = ""
    try:
        body_text = str(page.inner_text("body", timeout=2000) or "")
    except Exception:
        try:
            body_text = str(page.content() or "")[:200_000]
        except Exception:
            body_text = ""
    marker = next((item for item in _VISIBLE_CHALLENGE_MARKERS if item in body_text.lower()), "")
    if marker:
        return f"BUFF 页面要求安全验证（命中标记: {marker}）"
    return ""


def _response_has_challenge(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in _CHALLENGE_MARKERS)
