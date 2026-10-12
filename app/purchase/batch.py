"""Purchase batch responsibility."""

from __future__ import annotations
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
    PurchaseWriteResultUnknown,
    _PurchaseAttempt,
    _PurchaseAttemptStatus,
    _classify_failure,
    _mark_committed,
)
from app.purchase.identity import (
    _checkout_credential_identity,
)
from app.purchase.settlement import (
    _do_batch_wait_finalize_and_append,
)
from app.purchase.payment import (
    _supports_keyword_argument,
)


def _try_batch_buy(ctx: PurchaseContext) -> _PurchaseAttempt:
    configured_pay_method = (
        str(
            getattr(ctx.buff_client, "_pay_method", None)
            or ctx.buff_cfg.get("pay_method")
            or "alipay"
        )
        .strip()
        .lower()
    )
    if configured_pay_method != "wechat":
        return _PurchaseAttempt(
            _PurchaseAttemptStatus.SAFE_TO_FALLBACK,
            reason="当前支付方式不支持批量购买，且尚未发送写请求",
        )
    if not ctx._write_snapshot_is_fresh():
        return _PurchaseAttempt(_PurchaseAttemptStatus.FAILED, reason="写入前卖单快照已过期")
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
                "batch",
                ctx.goods_id,
                quantity=ctx.num_to_buy,
                credential_generation=identity.get("credential_generation", 0),
                credential_fingerprint=str(identity.get("credential_fingerprint") or ""),
                price=ctx.lowest_price,
            )
            checkout_intent_id = str(intent["intent_id"])

            def _on_batch_created(batch_id: str) -> None:
                update_checkout(
                    expected_intent_id=checkout_intent_id,
                    stage="batch_created",
                    batch_id=str(batch_id),
                    reason="批次已创建，正在获取支付二维码",
                )

            try:
                create_batch = ctx.buff_client.try_batch_buy
                if _supports_keyword_argument(create_batch, "on_created"):
                    batch_result = create_batch(
                        ctx.goods_id,
                        ctx.game_buff,
                        ctx.orders,
                        ctx.lowest_price,
                        ctx.num_to_buy,
                        on_created=_on_batch_created,
                    )
                else:
                    batch_result = create_batch(
                        ctx.goods_id, ctx.game_buff, ctx.orders, ctx.lowest_price, ctx.num_to_buy
                    )
            except BuffWriteResultUnknown as e:
                update_checkout(
                    expected_intent_id=checkout_intent_id,
                    stage="write_result_unknown",
                    batch_id=str(getattr(e, "batch_id", "") or ""),
                    reason=str(e) or "批量创建写请求结果未知",
                    last_error_type=type(e).__name__,
                )
                if ctx.log_fn:
                    ctx.log_fn(f"[Buff]   → 批量创建写请求结果未知: {e}", "warn")
                return _PurchaseAttempt(
                    _PurchaseAttemptStatus.UNKNOWN_AFTER_SEND,
                    reason=str(e) or "批量创建写请求结果未知",
                    batch_id=str(getattr(e, "batch_id", "") or ""),
                )
            except (BuffAuthExpired, BuffRequestBlocked):
                resolve_checkout("write_blocked_before_send", expected_intent_id=checkout_intent_id)
                raise
            except Exception as e:
                update_checkout(
                    expected_intent_id=checkout_intent_id,
                    stage="write_result_unknown",
                    reason=str(e) or "批量购买接口异常",
                    last_error_type=type(e).__name__,
                )
                if ctx.log_fn:
                    ctx.log_fn(f"[Buff]   → 批量购买接口异常: {e}", "warn")
                return _PurchaseAttempt(
                    _PurchaseAttemptStatus.UNKNOWN_AFTER_SEND, reason=str(e) or "批量购买接口异常"
                )
    if not batch_result or not batch_result.get("success"):
        attempt = _classify_failure(batch_result)
        if attempt.status in {
            _PurchaseAttemptStatus.UNKNOWN_AFTER_SEND,
            _PurchaseAttemptStatus.CREATED_NOT_PAID,
            _PurchaseAttemptStatus.COOLING_DOWN,
            _PurchaseAttemptStatus.RISK,
        }:
            update_checkout(
                expected_intent_id=checkout_intent_id,
                stage="batch_created_pending"
                if attempt.status is _PurchaseAttemptStatus.CREATED_NOT_PAID
                else "write_result_unknown",
                batch_id=attempt.batch_id,
                reason=attempt.reason,
            )
        else:
            resolve_checkout("known_not_created", expected_intent_id=checkout_intent_id)
        if ctx.log_fn:
            ctx.log_fn("[Buff]   → 批量锁单失败，接口未返回成功状态", "warn")
        return attempt
    batch_id = str(batch_result.get("batch_id") or "")
    pay_url = str(batch_result.get("pay_url") or "")
    if not batch_id:
        update_checkout(
            expected_intent_id=checkout_intent_id,
            stage="write_result_unknown",
            reason="批量接口返回成功但缺少批次号",
        )
        if ctx.log_fn:
            ctx.log_fn("[Buff]   → 批量接口返回成功但缺少批次号，停止且不改单件", "warn")
        return _PurchaseAttempt(
            _PurchaseAttemptStatus.UNKNOWN_AFTER_SEND, reason="批量接口返回成功但缺少批次号"
        )
    if not pay_url:
        identity = _checkout_credential_identity(ctx.buff_client)
        update_checkout(
            expected_intent_id=checkout_intent_id,
            stage="batch_created_pending",
            batch_id=batch_id,
            reason="批次已创建但缺少支付链接",
            credential_generation=identity.get("credential_generation", 0),
            credential_fingerprint=str(identity.get("credential_fingerprint") or ""),
        )
        if ctx.log_fn:
            ctx.log_fn("[Buff]   → 批次已创建但缺少支付链接，停止且不改单件", "warn")
        return _PurchaseAttempt(
            _PurchaseAttemptStatus.CREATED_NOT_PAID,
            reason="批次已创建但缺少支付链接",
            batch_id=batch_id,
        )
    if ctx.log_fn:
        ctx.log_fn(
            f"[Buff]   → 批量锁单成功 batch_id={batch_id} 数量={ctx.num_to_buy} 单价={ctx.lowest_price:.2f} 总价={batch_result.get('total_price', 0):.2f} 等待用户确认付款…",
            "info",
        )
    identity = _checkout_credential_identity(ctx.buff_client)
    update_checkout(
        expected_intent_id=checkout_intent_id,
        stage="payment_pending",
        batch_id=batch_id,
        credential_generation=identity.get("credential_generation", 0),
        credential_fingerprint=str(identity.get("credential_fingerprint") or ""),
        reason="批次已创建，等待付款确认",
    )
    try:
        paid = _do_batch_wait_finalize_and_append(
            ctx.buff_client,
            ctx.item,
            ctx.config,
            ctx.lowest_price,
            ctx.num_to_buy,
            ctx.goods_id,
            batch_id,
            checkout_intent_id,
            ctx.game_buff,
            pay_url,
            ctx.acc,
            ctx.set_pending_payment,
            ctx.wait_payment_confirm,
            ctx.confirm_payment,
            ctx.is_stop_requested,
            ctx.append_purchase,
            ctx.log_fn,
            market_price=ctx.ref_price,
            on_entering_payment=ctx.on_entering_payment,
        )
    except BuffWriteResultUnknown as e:
        update_checkout(
            expected_intent_id=checkout_intent_id,
            stage="batch_finalize_unknown",
            batch_id=batch_id,
            reason=str(e) or "批量核销写请求结果未知",
            last_error_type=type(e).__name__,
        )
        committed_amount = float(getattr(e, "committed_amount", 0.0) or 0.0)
        if committed_amount > 0:
            halted = PurchaseWriteResultUnknown(
                str(e) or "批量核销写请求结果未知", batch_id=batch_id
            )
            _mark_committed(
                halted, committed_amount, orders=int(getattr(e, "committed_orders", 1) or 1)
            )
            raise halted from e
        return _PurchaseAttempt(
            _PurchaseAttemptStatus.UNKNOWN_AFTER_SEND,
            reason=str(e) or "批量核销写请求结果未知",
            batch_id=batch_id,
        )
    if paid is None:
        update_checkout(
            expected_intent_id=checkout_intent_id,
            stage="batch_created_pending",
            batch_id=batch_id,
            reason="用户取消、超时或核销状态未确认",
        )
        return _PurchaseAttempt(
            _PurchaseAttemptStatus.CREATED_NOT_PAID,
            reason="批次已创建，用户取消、超时或核销状态未确认",
            batch_id=batch_id,
        )
    return _PurchaseAttempt(_PurchaseAttemptStatus.SUCCESS, amount=paid, batch_id=batch_id)
