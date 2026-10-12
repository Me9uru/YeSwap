"""Purchase single responsibility."""

from __future__ import annotations
from app.services.buff_client import first_order_at_price
from app.services.buff_checkout_guard import (
    begin_checkout,
    buff_activity_guard,
    resolve_checkout,
    update_checkout,
)
from app.services.buff_auth import get_buff_auth_lock
from buff import BuffAuthExpired, BuffRequestBlocked, BuffWriteResultUnknown
from app.purchase.models import (
    PurchaseContext,
    _PurchaseAttempt,
    _PurchaseAttemptStatus,
    _classify_failure,
)
from app.purchase.identity import (
    _checkout_credential_identity,
)
from app.purchase.settlement import (
    _do_wait_payment_and_append,
)
from app.purchase.payment import (
    _supports_keyword_argument,
)


def _try_single_buy(ctx: PurchaseContext) -> _PurchaseAttempt:
    if not ctx._write_snapshot_is_fresh():
        return _PurchaseAttempt(_PurchaseAttemptStatus.FAILED, reason="写入前卖单快照已过期")
    o = first_order_at_price(ctx.orders, ctx.lowest_price)
    if not o:
        return _PurchaseAttempt(_PurchaseAttemptStatus.FAILED, reason="未找到最低价卖单")
    p = float(o.get("price", 0))
    if not ctx._time_window_is_open():
        return _PurchaseAttempt(
            _PurchaseAttemptStatus.TIME_WINDOW_CLOSED, reason="购买时间窗已关闭"
        )
    with get_buff_auth_lock():
        with buff_activity_guard():
            if not ctx._time_window_is_open():
                return _PurchaseAttempt(
                    _PurchaseAttemptStatus.TIME_WINDOW_CLOSED,
                    reason="等待请求锁期间购买时间窗已关闭",
                )
            identity = _checkout_credential_identity(ctx.buff_client)
            intent = begin_checkout(
                "single",
                ctx.goods_id,
                sell_order_id=str(o.get("id") or ""),
                quantity=1,
                credential_generation=identity.get("credential_generation", 0),
                credential_fingerprint=str(identity.get("credential_fingerprint") or ""),
                price=p,
            )
            checkout_intent_id = str(intent["intent_id"])

            def _on_order_created(order_id: str) -> None:
                update_checkout(
                    expected_intent_id=checkout_intent_id,
                    stage="order_created",
                    order_id=str(order_id),
                    reason="订单已创建，正在获取支付链接",
                )

            if ctx.log_fn:
                ctx.log_fn(f"[Buff]   → 锁单 order_id={o.get('id')} price={o.get('price')}", "info")
            try:
                lock_order = ctx.buff_client.lock_and_get_pay_url
                if _supports_keyword_argument(lock_order, "on_created"):
                    result = lock_order(
                        ctx.game_buff,
                        ctx.goods_id,
                        o["id"],
                        o["price"],
                        on_created=_on_order_created,
                    )
                else:
                    result = lock_order(ctx.game_buff, ctx.goods_id, o["id"], o["price"])
            except BuffWriteResultUnknown as e:
                update_checkout(
                    expected_intent_id=checkout_intent_id,
                    stage="write_result_unknown",
                    order_id=str(getattr(e, "order_id", "") or ""),
                    reason=str(e) or "锁单写请求结果未知",
                    last_error_type=type(e).__name__,
                )
                if ctx.log_fn:
                    ctx.log_fn(f"[Buff]   → 锁单写请求结果未知: {e}", "warn")
                return _PurchaseAttempt(
                    _PurchaseAttemptStatus.UNKNOWN_AFTER_SEND,
                    reason=str(e) or "锁单写请求结果未知",
                    order_id=str(getattr(e, "order_id", "") or ""),
                )
            except (BuffAuthExpired, BuffRequestBlocked):
                resolve_checkout("write_blocked_before_send", expected_intent_id=checkout_intent_id)
                raise
            except Exception as e:
                update_checkout(
                    expected_intent_id=checkout_intent_id,
                    stage="write_result_unknown",
                    reason=str(e) or "锁单网络/接口异常",
                    last_error_type=type(e).__name__,
                )
                if ctx.log_fn:
                    ctx.log_fn(f"[Buff]   → 锁单网络/接口异常: {e}", "warn")
                return _PurchaseAttempt(
                    _PurchaseAttemptStatus.UNKNOWN_AFTER_SEND, reason=str(e) or "锁单网络/接口异常"
                )
    if not result or not result.get("success"):
        attempt = _classify_failure(result)
        if attempt.status in {
            _PurchaseAttemptStatus.UNKNOWN_AFTER_SEND,
            _PurchaseAttemptStatus.CREATED_NOT_PAID,
            _PurchaseAttemptStatus.COOLING_DOWN,
            _PurchaseAttemptStatus.RISK,
        }:
            update_checkout(
                expected_intent_id=checkout_intent_id,
                stage="order_created_pending"
                if attempt.status is _PurchaseAttemptStatus.CREATED_NOT_PAID
                else "write_result_unknown",
                order_id=attempt.order_id,
                reason=attempt.reason,
            )
        else:
            resolve_checkout("known_not_created", expected_intent_id=checkout_intent_id)
        if ctx.log_fn:
            code_str = result.get("code") if result else "未知"
            msg_str = result.get("msg", "无响应内容") if result else "请求失败或超时"
            if result and result.get("created") is True:
                ctx.log_fn(
                    f"[Buff]   → 订单已创建，但付款链接获取失败 order_id={attempt.order_id or '未知'} code={code_str} reason={msg_str}",
                    "warn",
                )
            else:
                ctx.log_fn(f"[Buff]   → 锁单失败 code={code_str} msg={msg_str}", "warn")
        return attempt
    order_id = str(result.get("order_id") or "")
    pay_url = str(result.get("pay_url") or "")
    if not order_id:
        update_checkout(
            expected_intent_id=checkout_intent_id,
            stage="write_result_unknown",
            reason="锁单返回成功但缺少订单号",
        )
        return _PurchaseAttempt(
            _PurchaseAttemptStatus.UNKNOWN_AFTER_SEND, reason="锁单返回成功但缺少订单号"
        )
    if not pay_url:
        identity = _checkout_credential_identity(ctx.buff_client)
        update_checkout(
            expected_intent_id=checkout_intent_id,
            stage="order_created_pending",
            order_id=order_id,
            reason="订单已创建但未取得支付链接",
            credential_generation=identity.get("credential_generation", 0),
            credential_fingerprint=str(identity.get("credential_fingerprint") or ""),
        )
        return _PurchaseAttempt(
            _PurchaseAttemptStatus.CREATED_NOT_PAID,
            reason="订单已创建但未取得支付链接",
            order_id=order_id,
        )
    if ctx.log_fn:
        ctx.log_fn(f"[Buff]   → 锁单成功 order_id={order_id} 等待用户确认付款…", "info")
    identity = _checkout_credential_identity(ctx.buff_client)
    update_checkout(
        expected_intent_id=checkout_intent_id,
        stage="payment_pending",
        order_id=order_id,
        credential_generation=identity.get("credential_generation", 0),
        credential_fingerprint=str(identity.get("credential_fingerprint") or ""),
        reason="订单已创建，等待付款确认",
    )
    paid = _do_wait_payment_and_append(
        ctx.buff_client,
        ctx.item,
        ctx.config,
        p,
        1,
        ctx.goods_id,
        pay_url,
        result.get("pay_type") or "alipay",
        order_id,
        checkout_intent_id,
        ctx.acc,
        ctx.set_pending_payment,
        ctx.wait_payment_confirm,
        ctx.confirm_payment,
        ctx.is_stop_requested,
        ctx.append_purchase,
        ctx.log_fn,
        ctx.game_buff,
        sell_order_id=str(o.get("id") or ""),
        market_price=ctx.ref_price,
        on_entering_payment=ctx.on_entering_payment,
    )
    if paid is None:
        update_checkout(
            expected_intent_id=checkout_intent_id,
            stage="order_created_pending",
            order_id=order_id,
            reason="用户取消、超时或付款状态未确认",
        )
        return _PurchaseAttempt(
            _PurchaseAttemptStatus.CREATED_NOT_PAID,
            reason="订单已创建，用户取消、超时或付款状态未确认",
            order_id=order_id,
        )
    return _PurchaseAttempt(_PurchaseAttemptStatus.SUCCESS, amount=paid, order_id=order_id)
