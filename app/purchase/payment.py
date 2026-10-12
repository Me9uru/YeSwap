"""Purchase payment responsibility."""

from __future__ import annotations
import inspect
import threading
from typing import Any, Callable, Dict, Optional
from app.notifications.service import send_pushplus, build_payment_notify_content, wait_email_command


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
    unit_price: float,
    num: int,
    pay_url: str,
    pay_type: str,
    order_id: str,
    acc: float,
    set_pending_payment: callable,
    wait_payment_confirm: callable,
    confirm_payment: callable,
    is_stop_requested: callable,
    log_fn: Optional[Callable[[str, str], None]],
    on_entering_payment: Optional[Callable[[], None]] = None,
) -> bool:
    """Handle notification and wait for user payment confirmation.
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
        push_token = (notify_cfg.get("pushplus_token") or "").strip()
        if push_token:
            sell_ratio = None
            value_ratio = item.get("value_ratio")
            try:
                rv = item.get("ratio")
                if rv is not None:
                    if isinstance(rv, str):
                        rv = rv.strip().replace("%", "")
                    sell_ratio = float(rv)
            except (TypeError, ValueError):
                pass
            mhn = (item.get("steam_market_name") or item.get("name") or "").strip()
            sl = item.get("steam_link")
            content = build_payment_notify_content(
                name,
                unit_price,
                pay_url,
                pay_type,
                acc,
                sell_ratio=sell_ratio,
                num=num,
                value_ratio=value_ratio,
                steam_market_hash_name=mhn,
                steam_link=sl,
            )
            try:
                if send_pushplus(push_token, "Buff 待付款", content):
                    if log_fn:
                        log_fn("[Buff]   → PushPlus 推送已发送", "info")
                else:
                    if log_fn:
                        log_fn("[Buff]   → PushPlus 推送发送失败 (返回False)", "warn")
            except Exception as e:
                if log_fn:
                    log_fn(f"[Buff]   → PushPlus 推送发送异常: {e}", "warn")
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
