import ast
import tomllib
from pathlib import Path

from app.runtime_env import (
    get_bind_host,
    get_browser_channel,
    get_local_url,
    get_port,
    web_build_enabled,
)


def test_web_runtime_does_not_depend_on_desktop_shell():
    data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    dependencies = data["project"]["dependencies"]
    tree = ast.parse(Path("app/main.py").read_text(encoding="utf-8"))
    imported_modules = {
        node.names[0].name.split(".", 1)[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import) and node.names
    }

    assert not any(dependency.lower().startswith("pywebview") for dependency in dependencies)
    assert imported_modules.isdisjoint({"webview", "webbrowser"})
    assert "optional-dependencies" not in data["project"]


def test_main_starts_uvicorn_without_opening_a_desktop_window(monkeypatch, capsys):
    import app.main as main_module

    calls = []
    monkeypatch.setattr(main_module, "get_bind_host", lambda: "0.0.0.0")
    monkeypatch.setattr(main_module, "get_port", lambda: 3000)
    monkeypatch.setattr(main_module, "build_web_if_stale", lambda: None)
    monkeypatch.setattr(main_module.uvicorn, "run", lambda *args, **kwargs: calls.append((args, kwargs)))

    main_module.main()

    assert calls == [(("app.api:app",), {"host": "0.0.0.0", "port": 3000, "log_level": "warning"})]
    assert "http://127.0.0.1:3000" in capsys.readouterr().out


def test_web_backend_defaults_to_localhost_and_allows_lan_binding():
    assert get_bind_host(env={}) == "127.0.0.1"
    assert get_bind_host(env={"AETHERSWAP_HOST": "0.0.0.0"}) == "0.0.0.0"


def test_env_overrides_port_and_wildcard_host_uses_local_access_url():
    assert get_port(env={"AETHERSWAP_PORT": "3000"}) == 3000
    assert get_local_url("0.0.0.0", 28472) == "http://127.0.0.1:28472"


def test_web_build_flag_defaults_on_and_accepts_off_switches():
    assert web_build_enabled(env={}) is True
    assert web_build_enabled(env={"AETHERSWAP_WEB_BUILD": "1"}) is True
    assert web_build_enabled(env={"AETHERSWAP_WEB_BUILD": "no"}) is False
    assert web_build_enabled(env={"AETHERSWAP_WEB_BUILD": "OFF"}) is False


def test_browser_channel_accepts_supported_host_browsers_only():
    assert get_browser_channel({"AETHERSWAP_BROWSER_CHANNEL": "Chrome"}) == "chrome"
    assert get_browser_channel({"AETHERSWAP_BROWSER_CHANNEL": "firefox"}) == ""
