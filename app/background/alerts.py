"""Alerts."""

from app.services.lifecycle import WorkerStopped
import time
from app.runtime.state import log
from app.configuration.repository import load_app_config_validated
from app.notifications.service import send_pushplus


_worker_alert_last: dict = {}


_WORKER_ALERT_COOLDOWN = 3600


def _worker_alert(worker_name: str, error: Exception) -> None:
    """发送 PushPlus 告警，每个 worker 每小时至多发一次。"""
    now = time.time()
    last = _worker_alert_last.get(worker_name, 0.0)
    if now - last < _WORKER_ALERT_COOLDOWN:
        return
    try:
        cfg = load_app_config_validated()
        token = (cfg.get("notify") or {}).get("pushplus_token", "") or ""
        if not token:
            return
        msg = str(error)[:200] if error else "未知异常"
        send_pushplus(
            token,
            f"[Worker异常] {worker_name}",
            f"后台任务 <b>{worker_name}</b> 发生异常，已自动重试。<br>错误信息：{msg}",
        )
        _worker_alert_last[worker_name] = now
        log(f"[{worker_name}] 异常告警已发送", "info", category="alert")
    except WorkerStopped:
        raise
    except Exception:
        pass
