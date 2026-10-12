"""启动时的前端资源构建：仅在产物过期时构建，失败不阻塞后端启动。"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

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


def test_stale_bundle_triggers_npm_build(tmp_path, monkeypatch):
    web = _stale_web(tmp_path, monkeypatch)
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(main_module.subprocess, "run", fake_run)

    main_module.build_web_if_stale()

    assert len(commands) == 1, "源码比产物新时应触发一次构建"
    command = commands[0]
    assert command[0] == "/usr/bin/npm"
    assert command[-2:] == ["run", "build"]
    assert str(web) in command


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


@pytest.mark.parametrize(
    "scenario",
    [
        "build_failure",
        "build_failure_after_dist_emptied",
        "missing_npm",
        "missing_node_modules",
    ],
)
def test_failed_or_skipped_builds_never_block_startup(tmp_path, monkeypatch, scenario):
    """构建失败或缺前置条件时只告警并返回：不抛异常，已有页面继续可用。"""
    web = _stale_web(tmp_path, monkeypatch)
    commands = []

    def failing_run(command, **kwargs):
        commands.append(command)
        if scenario == "build_failure_after_dist_emptied":
            (web / "dist" / "index.html").unlink()  # vite emptyOutDir 清空后失败
        return subprocess.CompletedProcess(command, 1, "", "boom")

    monkeypatch.setattr(main_module.subprocess, "run", failing_run)
    if scenario == "missing_npm":
        monkeypatch.setattr(main_module.shutil, "which", lambda name: None)
    elif scenario == "missing_node_modules":
        (web / "node_modules").rmdir()

    main_module.build_web_if_stale()

    if scenario.startswith("build_failure"):
        assert len(commands) == 1, "构建已尝试且失败"
    else:
        assert commands == [], "缺少构建前置条件时应直接跳过"
    if scenario != "build_failure_after_dist_emptied":
        assert (web / "dist" / "index.html").is_file(), "失败不阻塞启动，已有页面继续可用"
