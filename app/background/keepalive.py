"""Keepalive."""

from app.services.lifecycle import wait, stoppable_worker, WorkerStopped
from app.runtime.state import log
from app.configuration.repository import load_app_config_validated
from app.background.safety import (
    _session_keepalive_is_safe,
)


def _session_keepalive_enabled(cfg: dict) -> bool:
    return bool((cfg.get("system") or {}).get("buff_session_keepalive_enabled", False))


@stoppable_worker
def session_keepalive_worker() -> None:
    from app.services.buff_auth import try_buff_auto_relogin

    while True:
        try:
            cfg = load_app_config_validated()
            if not _session_keepalive_enabled(cfg):
                # Poll the opt-in switch without touching either remote service.
                wait(60)
                continue

            sys_cfg = cfg.get("system") or {}
            interval_h = float(sys_cfg.get("session_keepalive_hours", 4.0))
            if interval_h <= 0:
                log(
                    "keepalive: 已开启但 system.session_keepalive_hours<=0，等待配置修正",
                    "debug",
                    category="keepalive",
                )
                wait(60)
                continue

            # The first request waits the complete configured interval; startup
            # no longer forces a browser visit after five minutes.
            wait(interval_h * 3600)

            cfg = load_app_config_validated()
            if not _session_keepalive_enabled(cfg):
                continue
            while not _session_keepalive_is_safe():
                log("keepalive: 流水线或结账正在进行，延后会话保活", "debug", category="keepalive")
                wait(60)
                cfg = load_app_config_validated()
                if not _session_keepalive_enabled(cfg):
                    break
            if not _session_keepalive_enabled(cfg):
                continue

            # Re-check immediately before launching the browser to close the
            # race between the wait loop and a newly started pipeline.
            if not _session_keepalive_is_safe():
                continue
            log("keepalive: 开始本轮 Buff 后台会话保活...", "info", category="keepalive")
            buff_ok, _buff_status, buff_msg = try_buff_auto_relogin()
            if not buff_ok:
                log(f"keepalive: Buff 保活未完成: {buff_msg}", "warn", category="keepalive")
            else:
                log(f"keepalive: Buff 保活成功: {buff_msg}", "info", category="keepalive")
            log("keepalive: 本轮 Buff 后台会话保活已完成", "info", category="keepalive")
        except WorkerStopped:
            raise
        except Exception as e:
            log(f"keepalive: worker 异常 {e}, 15 分钟后重试", "error", category="keepalive")
            wait(900)
