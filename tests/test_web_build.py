"""启动时的前端资源构建：仅在产物过期时构建，失败不阻塞后端启动。"""

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import main as main_module


def _write(path: Path, mtime: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    os.utime(path, (mtime, mtime))


def _web_dir(tmp_path: Path, built_at: float, source_at: float) -> Path:
    """A web/ tree with a bundle built at ``built_at`` and sources at ``source_at``."""
    web = tmp_path / "web"
    _write(web / "dist" / "index.html", built_at)
    _write(web / "index.html", source_at)
    _write(web / "package.json", source_at)
    _write(web / "vite.config.js", source_at)
    _write(web / "src" / "views" / "Strategies.vue", source_at)
    (web / "node_modules").mkdir(parents=True, exist_ok=True)
    return web


def _stale_web(tmp_path: Path, monkeypatch) -> Path:
    web = _web_dir(tmp_path, built_at=1_000, source_at=2_000)
    monkeypatch.setattr(main_module, "WEB_DIR", web)
    monkeypatch.setattr(main_module.shutil, "which", lambda name: "/usr/bin/npm")
    monkeypatch.delenv("AETHERSWAP_WEB_BUILD", raising=False)
    return web


def test_bundle_is_stale_only_when_sources_are_newer(tmp_path):
    web = _web_dir(tmp_path, built_at=2_000, source_at=1_000)
    assert main_module.web_build_required(web) is False

    _write(web / "src" / "views" / "Strategies.vue", 3_000)
    assert main_module.web_build_required(web) is True


def test_missing_bundle_requires_a_build(tmp_path):
    web = _web_dir(tmp_path, built_at=2_000, source_at=1_000)
    (web / "dist" / "index.html").unlink()
    assert main_module.web_build_required(web) is True


def test_absent_sources_do_not_trigger_repeated_builds(tmp_path):
    """只带预构建产物、没有前端源码的部署不应每次启动都尝试构建。"""
    web = tmp_path / "packaged"
    _write(web / "dist" / "index.html", 2_000)
    assert main_module.web_build_required(web) is False


def test_stale_bundle_triggers_npm_build(tmp_path, monkeypatch, capsys):
    web = _stale_web(tmp_path, monkeypatch)
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(main_module.subprocess, "run", fake_run)

    main_module.build_web_if_stale()

    assert calls == [
        (
            ["/usr/bin/npm", "--prefix", str(web), "run", "build"],
            {
                "cwd": main_module.PROJECT_ROOT,
                "capture_output": True,
                "text": True,
                "timeout": main_module.BUILD_TIMEOUT_SECONDS,
            },
        )
    ]
    assert "前端资源已更新。" in capsys.readouterr().out


def test_fresh_bundle_never_invokes_npm(tmp_path, monkeypatch, capsys):
    _web_dir(tmp_path, built_at=2_000, source_at=1_000)
    monkeypatch.setattr(main_module, "WEB_DIR", tmp_path / "web")
    monkeypatch.delenv("AETHERSWAP_WEB_BUILD", raising=False)

    def unexpected(*args, **kwargs):
        raise AssertionError("产物是最新的，不应执行构建")

    monkeypatch.setattr(main_module.subprocess, "run", unexpected)

    main_module.build_web_if_stale()

    assert capsys.readouterr().out == ""


def test_disabled_env_never_invokes_npm(tmp_path, monkeypatch, capsys):
    _stale_web(tmp_path, monkeypatch)
    monkeypatch.setenv("AETHERSWAP_WEB_BUILD", "0")

    def unexpected(*args, **kwargs):
        raise AssertionError("AETHERSWAP_WEB_BUILD=0 时不应执行构建")

    monkeypatch.setattr(main_module.subprocess, "run", unexpected)

    main_module.build_web_if_stale()

    assert capsys.readouterr().out == ""


def test_failed_build_warns_and_keeps_serving_current_bundle(
    tmp_path, monkeypatch, capsys
):
    _stale_web(tmp_path, monkeypatch)

    def failing_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 1, "", "vite: 构建失败\n")

    monkeypatch.setattr(main_module.subprocess, "run", failing_run)

    main_module.build_web_if_stale()

    captured = capsys.readouterr()
    assert "警告：前端资源构建失败。" in captured.err
    assert "npm --prefix web run build" in captured.err
    assert "vite: 构建失败" in captured.err
    assert "页面将继续使用 web/dist 中已有的内容。" in captured.err


def test_failed_build_reports_missing_bundle(tmp_path, monkeypatch, capsys):
    web = _stale_web(tmp_path, monkeypatch)

    def failing_run(command, **kwargs):
        (web / "dist" / "index.html").unlink()  # vite emptyOutDir 清空后失败
        return subprocess.CompletedProcess(command, 1, "", "boom")

    monkeypatch.setattr(main_module.subprocess, "run", failing_run)

    main_module.build_web_if_stale()

    assert "当前 web/dist 不可用" in capsys.readouterr().err


def test_missing_npm_warns_with_manual_command(tmp_path, monkeypatch, capsys):
    _stale_web(tmp_path, monkeypatch)
    monkeypatch.setattr(main_module.shutil, "which", lambda name: None)

    main_module.build_web_if_stale()

    assert "未找到 npm" in capsys.readouterr().err


def test_missing_node_modules_warns_with_install_command(tmp_path, monkeypatch, capsys):
    web = _stale_web(tmp_path, monkeypatch)
    web.joinpath("node_modules").rmdir()

    main_module.build_web_if_stale()

    captured = capsys.readouterr()
    assert "缺少 web/node_modules" in captured.err
    assert "npm --prefix web ci" in captured.err


def test_launch_refreshes_bundle_before_serving(monkeypatch):
    events = []
    monkeypatch.setattr(
        main_module, "build_web_if_stale", lambda: events.append("build")
    )
    monkeypatch.setattr(
        main_module.uvicorn, "run", lambda *args, **kwargs: events.append("serve")
    )

    main_module.main()

    assert events == ["build", "serve"]
