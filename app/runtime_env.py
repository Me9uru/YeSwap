"""Local launch configuration helpers."""
from __future__ import annotations

import os
from typing import Mapping, Optional


DEFAULT_PORT = 28472
DEFAULT_HOST = "127.0.0.1"


def _clean(value: Optional[str]) -> str:
    return (value or "").strip()


def _env_value(*names: str, env: Optional[Mapping[str, str]] = None) -> str:
    source = os.environ if env is None else env
    for name in names:
        value = _clean(source.get(name))
        if value:
            return value
    return ""


def get_bind_host(env: Optional[Mapping[str, str]] = None) -> str:
    return _env_value("AETHERSWAP_HOST", env=env) or DEFAULT_HOST


def get_port(env: Optional[Mapping[str, str]] = None) -> int:
    raw = _env_value("AETHERSWAP_PORT", env=env)
    if not raw:
        return DEFAULT_PORT
    try:
        port = int(raw)
    except ValueError:
        return DEFAULT_PORT
    return port if 0 < port < 65536 else DEFAULT_PORT


def get_local_url(host: str, port: int) -> str:
    browser_host = "127.0.0.1" if host in {"0.0.0.0", "::"} else host
    return f"http://{browser_host}:{port}"


def web_build_enabled(env: Optional[Mapping[str, str]] = None) -> bool:
    """Whether the launcher rebuilds the Vue bundle when it is stale.

    Hosts that ship a prebuilt ``web/dist`` can turn this off to keep startup
    from ever invoking Node.js.
    """
    value = _env_value("AETHERSWAP_WEB_BUILD", env=env).lower()
    return value not in {"0", "false", "no", "off"}


def get_browser_channel(env: Optional[Mapping[str, str]] = None) -> str:
    """Return an optional Playwright browser channel such as ``chrome``.

    An empty value uses Playwright's bundled Chromium. Host launchers can
    select an installed browser and avoid maintaining a second browser download.
    """
    value = _env_value("AETHERSWAP_BROWSER_CHANNEL", env=env).lower()
    return value if value in {"chrome", "msedge", "chromium"} else ""
