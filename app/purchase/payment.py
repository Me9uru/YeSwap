"""Purchase payment responsibility."""

from __future__ import annotations
import inspect
import threading
from typing import Any, Callable, Dict, Optional
from app.notifications.service import wait_email_command
from buff import BUY_ORDER_PAY_FAILED, BUY_ORDER_PAY_PAID
from utils.delay import jittered_sleep


# 免密支付确认窗口：订单创建且拿不到支付链接后，立即查询一次付款状态，
# 再按固定节奏重试；实际间隔还会叠加 BUFF 请求节流的最小间隔。
PASSWORDLESS_PAYMENT_POLL_DELAYS = (0.0, 1.5, 2.5, 4.0, 5.0, 6.0, 8.0)


def _confirm_passwordless_payment(
    buff_client: Any,
    order_id: str,
    game: str,
    is_stop_requested: Optional[Callable[[], bool]],
    log_fn: Optional[Callable[[str, str], None]],
) -> Optional[str]:
    """确认免密（自动扣款）支付的最终结果。

    只读轮询 BUFF 买单付款状态；返回 ``BUY_ORDER_PAY_PAID`` /
    ``BUY_ORDER_PAY_FAILED``，等待窗口内未确认或查询异常时返回 ``None``。
    只有明确已支付才允许继续结算，其余结果都必须退回人工对账门禁。
    """

    for delay in PASSWORDLESS_PAYMENT_POLL_DELAYS:
        if is_stop_requested is not None and is_stop_requested():
            return None
        if delay > 0:
            jittered_sleep(delay, is_cancelled=is_stop_requested)
            if is_stop_requested is not None and is_stop_requested():
                return None
        try:
            state = buff_client.get_buy_order_pay_state(str(order_id), game)
        except Exception as exc:
            if log_fn:
                log_fn(
                    f"[Buff]   → 免密支付状态查询异常 ({type(exc).__name__}: {exc})，保持人工对账门禁",
                    "warn",
                )
            return None
        if state == BUY_ORDER_PAY_PAID:
            return BUY_ORDER_PAY_PAID
        if state == BUY_ORDER_PAY_FAILED:
            return BUY_ORDER_PAY_FAILED
    if log_fn:
        log_fn(
            "[Buff]   → 免密支付状态未在等待窗口内确认，保持人工对账门禁",
            "warn",
        )
    return None


def _supports_keyword_argument(callable_obj: Any, name: str) -> bool:
    try:
        parameters = inspect.signature(callable_obj).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        parameter.name == name or parameter.kind is inspect.Parameter.VAR_KEYWORD
        for parameter in parameters
    )


def _do_payment_notify_and_wait(
    item: Dict[str, Any],
    config: dict,
    pay_url: str,
    pay_type: str,
    order_id: str,
    set_pending_payment: callable,
    wait_payment_confirm: callable,
    confirm_payment: callable,
    is_stop_requested: callable,
    log_fn: Optional[Callable[[str, str], None]],
    on_entering_payment: Optional[Callable[[], None]] = None,
) -> bool:
    """Handle the pending-payment state and wait for user payment confirmation.
    Returns True if user confirmed, False on cancel/timeout/stop.
    """
    name = item.get("name", "")
    set_pending_payment(
        {
            "pay_url": pay_url,
            "pay_type": pay_type,
            "name": name,
            "order_id": order_id,
        }
    )
    try:
        if on_entering_payment:
            on_entering_payment()
        notify_cfg = config.get("notify") or {}
        email_user = (notify_cfg.get("email_user") or "").strip()
        email_pass = (notify_cfg.get("email_pass") or "").strip()
        timeout_sec = int(notify_cfg.get("email_timeout_seconds", 300))
        if email_user and email_pass:

            def _email_waiter() -> None:
                res = wait_email_command(
                    config,
                    timeout_seconds=timeout_sec,
                    is_stop_requested=is_stop_requested,
                    log_fn=log_fn,
                )
                if _supports_keyword_argument(confirm_payment, "expected_order_id"):
                    accepted = confirm_payment(
                        res == "success",
                        expected_order_id=str(order_id),
                    )
                    if accepted is False and log_fn:
                        log_fn(
                            f"[Buff]   → 忽略已结束订单 {order_id} 的过期邮箱确认结果",
                            "warn",
                        )
                else:
                    confirm_payment(res == "success")

            t = threading.Thread(target=_email_waiter, daemon=True)
            t.start()
            ok = wait_payment_confirm()
        else:
            ok = wait_payment_confirm(timeout_seconds=timeout_sec)
        if log_fn:
            log_fn(f"[Buff]   → 用户确认={'成功' if ok else '取消/失败'}", "info")
        return ok
    finally:
        set_pending_payment(None)
