"""Runtime, task monitoring and application shutdown endpoints."""

import time
from fastapi import APIRouter, BackgroundTasks
from app.services.task_queue import get_task_queue
from app.services.lifecycle import stop_event
from app.runtime.state import log, request_stop

router = APIRouter()


@router.get("/api/tasks")
def api_tasks(limit: int = 50):
    q = get_task_queue()
    return {"tasks": q.list_tasks(limit=limit), "active": q.active_count()}


@router.get("/api/tasks/{task_id}")
def api_task_detail(task_id: str):
    q = get_task_queue()
    info = q.get_task(task_id)
    if info is None:
        return {"ok": False, "error": "任务不存在"}
    return {"ok": True, "task": info}


@router.post("/api/system/shutdown")
def shutdown_system(background_tasks: BackgroundTasks):
    def _do_shutdown():
        time.sleep(1)
        log("system: 收到前端关机请求，正在通知所有 worker 停止...", "info", category="system")
        stop_event.set()
        request_stop()
        time.sleep(1)
        log("system: 正在退出进程...", "info", category="system")
        import os, signal

        os.kill(os.getpid(), signal.SIGINT)

    background_tasks.add_task(_do_shutdown)
    return {"ok": True, "message": "正在彻底退出系统..."}
