"""Process entry point shared by ``run.py`` and ``python -m app``."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import uvicorn

from app.runtime_env import (
    get_bind_host,
    get_local_url,
    get_port,
    web_build_enabled,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = PROJECT_ROOT / "web"
# Frontend inputs that make the served bundle stale once they are newer.
WEB_SOURCES = ("src", "index.html", "package.json", "vite.config.js")
BUILD_TIMEOUT_SECONDS = 300


def _newest_mtime(path: Path) -> float:
    if path.is_dir():
        return max(
            (item.stat().st_mtime for item in path.rglob("*") if item.is_file()),
            default=0.0,
        )
    return path.stat().st_mtime if path.is_file() else 0.0


def web_build_required(web_dir: Path) -> bool:
    """Whether the served bundle is missing or older than the frontend sources."""
    index = web_dir / "dist" / "index.html"
    if not index.is_file():
        return True
    built_at = index.stat().st_mtime
    return any(_newest_mtime(web_dir / name) > built_at for name in WEB_SOURCES)


def _warn_build_skipped(headline: str, remedy: str, detail: str = "") -> None:
    print(f"警告：{headline}", file=sys.stderr)
    print(remedy, file=sys.stderr)
    if detail:
        print(detail, file=sys.stderr)
    if (WEB_DIR / "dist" / "index.html").is_file():
        print("页面将继续使用 web/dist 中已有的内容。", file=sys.stderr)
    else:
        print("当前 web/dist 不可用，页面需要构建成功后才能打开。", file=sys.stderr)


def build_web_if_stale() -> None:
    """Refresh the served bundle when needed, without blocking startup on failure.

    The backend keeps serving trades and reports regardless of Node.js being
    installed, so a failed frontend build is reported and skipped.
    """
    if not web_build_enabled() or not web_build_required(WEB_DIR):
        return
    npm = shutil.which("npm")
    if npm is None:
        _warn_build_skipped(
            "前端源码比 web/dist 新，但未找到 npm，已跳过构建。",
            "安装 Node.js 22+ 后重新启动，或手动执行 npm --prefix web run build。",
        )
        return
    if not (WEB_DIR / "node_modules").is_dir():
        _warn_build_skipped(
            "前端源码比 web/dist 新，但缺少 web/node_modules，已跳过构建。",
            "先执行 npm --prefix web ci，再重新启动。",
        )
        return
    print("检测到前端源码更新，正在构建 web/dist ...")
    try:
        result = subprocess.run(
            [npm, "--prefix", str(WEB_DIR), "run", "build"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=BUILD_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        _warn_build_skipped(
            "前端资源构建失败。",
            "请手动执行 npm --prefix web run build 查看详情。",
            detail=str(error),
        )
        return
    if result.returncode != 0:
        output = (result.stderr or result.stdout or "").strip().splitlines()
        _warn_build_skipped(
            "前端资源构建失败。",
            "请手动执行 npm --prefix web run build 查看详情。",
            detail="\n".join(output[-8:]),
        )
        return
    print("前端资源已更新。")


def main() -> None:
    host = get_bind_host()
    port = get_port()
    local_url = get_local_url(host, port)
    build_web_if_stale()
    print(f"AetherSwap 已启动，请在浏览器访问 {local_url}")
    uvicorn.run("app.api:app", host=host, port=port, log_level="warning")


if __name__ == "__main__":
    main()
