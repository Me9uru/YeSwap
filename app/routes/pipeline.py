"""Pipeline start/stop routes."""
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.workflows.purchase import get_pipeline_start_blocker, start_pipeline
from app.services.buff_checkout_guard import (
    BuffCheckoutGuardMismatch,
    acknowledge_checkout,
)
from app.runtime.state import get_status, request_stop, set_status, log
router = APIRouter()
class ConfigBody(BaseModel):
    config: dict
    acknowledge_buff_reconciliation: bool = False
    buff_reconciliation_intent_id: str = ""


class CheckoutReconcileBody(BaseModel):
    intent_id: str
    outcome: Literal["cancelled_or_unpaid", "handled_externally"]
@router.post("/api/pipeline/start")
def api_pipeline_start(body: ConfigBody):
    blocker = get_pipeline_start_blocker()
    if (
        blocker.get("code") == "BUFF_RECONCILIATION_REQUIRED"
        and not body.acknowledge_buff_reconciliation
    ):
        return {
            "ok": False,
            "reconciliation_required": True,
            "code": blocker["code"],
            "error": blocker["message"],
            "checkout": blocker.get("checkout") or {},
        }
    if blocker and blocker.get("code") != "BUFF_RECONCILIATION_REQUIRED":
        return {
            "ok": False,
            "code": blocker.get("code"),
            "error": blocker.get("message"),
        }
    if not start_pipeline(
        body.config,
        acknowledge_buff_reconciliation=body.acknowledge_buff_reconciliation,
        buff_reconciliation_intent_id=body.buff_reconciliation_intent_id,
    ):
        blocker = get_pipeline_start_blocker()
        if blocker:
            return {
                "ok": False,
                "reconciliation_required": (
                    blocker.get("code") == "BUFF_RECONCILIATION_REQUIRED"
                ),
                "code": blocker.get("code"),
                "error": blocker.get("message"),
                "checkout": blocker.get("checkout") or {},
            }
        log("买入流水线已在运行，忽略重复启动请求", level="warn", category="pipeline")
        return {"ok": False, "already_running": True, "error": "买入流水线已在运行，请勿重复启动"}
    return {"ok": True}


@router.get("/api/pipeline/buff_checkout_guard")
def api_buff_checkout_guard():
    blocker = get_pipeline_start_blocker()
    return {
        "reconciliation_required": (
            blocker.get("code") == "BUFF_RECONCILIATION_REQUIRED"
        ),
        "checkout": blocker.get("checkout") or {},
    }


# 人工核对解决的是「写结果未知 / 订单已创建未付款」这类待对账歧义；核对成功
# 后必须同步复位对应的停机 step，否则 background/safety 的后台 BUFF 读取门禁
# （收货、库存等）会继续被已核对过的 step 永久挡住。冷却与异常崩溃不属于可随
# 核对解除的范畴，保持原样。
_RECONCILE_RELEASABLE_STEPS = frozenset(
    {
        "BUFF_WRITE_RESULT_UNKNOWN",
        "BUFF_ORDER_CREATED_PENDING",
    }
)


@router.post("/api/pipeline/buff_checkout_guard/reconcile")
def api_reconcile_buff_checkout(body: CheckoutReconcileBody):
    intent_id = body.intent_id.strip()
    if not intent_id:
        raise HTTPException(status_code=400, detail="缺少 checkout intent_id")
    blocker = get_pipeline_start_blocker()
    checkout = blocker.get("checkout") or {}
    if blocker.get("code") != "BUFF_RECONCILIATION_REQUIRED":
        raise HTTPException(status_code=409, detail="当前没有待对账的 BUFF 订单")
    if str(checkout.get("intent_id") or "") != intent_id:
        raise HTTPException(status_code=409, detail="待对账订单已变化，请刷新后重试")
    reason = (
        "user_confirmed_cancelled_or_unpaid"
        if body.outcome == "cancelled_or_unpaid"
        else "user_confirmed_handled_externally"
    )
    try:
        acknowledge_checkout(intent_id, reason)
    except BuffCheckoutGuardMismatch as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    released = (get_status() or {}).get("step") in _RECONCILE_RELEASABLE_STEPS
    if released:
        set_status("stopped", "已人工对账，等待启动")
    log(
        f"BUFF checkout 已人工对账并解除门禁 intent_id={intent_id} "
        f"outcome={body.outcome}" + ("；运行状态已复位" if released else ""),
        level="warn",
        category="buff",
    )
    return {"ok": True}
@router.post("/api/pipeline/stop")
def api_pipeline_stop():
    request_stop()
    log("接收到停止运行指令，正在终止任务...", level="warn", category="system")
    set_status("stopped", "正在停止并清理...")
    return {"ok": True}
