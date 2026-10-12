"""Purchase models responsibility."""

from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from enum import Enum
from typing import Any, Dict, Optional
from buff import BuffVerificationRequired


BUFF_ORDERS_CACHE_TTL_SECONDS = 3.0


TIME_WINDOW_CLOSED = object()


CURRENCY_QUANTUM = Decimal("0.01")


def _currency_amount(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(
        CURRENCY_QUANTUM,
        rounding=ROUND_HALF_UP,
    )


def _affordable_quantity(
    target_balance: Any,
    accumulated: Any,
    unit_price: Any,
) -> int:
    price = _currency_amount(unit_price)
    if price <= 0:
        return 0
    remaining = _currency_amount(target_balance) - _currency_amount(accumulated)
    if remaining <= 0:
        return 0
    return max(0, int(remaining // price))


class _PurchaseAttemptStatus(Enum):
    SUCCESS = "success"
    SAFE_TO_FALLBACK = "safe_to_fallback"
    CREATED_NOT_PAID = "created_not_paid"
    UNKNOWN_AFTER_SEND = "unknown_after_send"
    COOLING_DOWN = "cooling_down"
    RISK = "risk"
    TIME_WINDOW_CLOSED = "time_window_closed"
    FAILED = "failed"


class PurchaseFlowHalted(RuntimeError):
    """A checkout outcome that forbids any subsequent purchase write."""

    def __init__(
        self,
        message: str,
        *,
        order_id: str = "",
        batch_id: str = "",
    ) -> None:
        super().__init__(message)
        self.order_id = order_id
        self.batch_id = batch_id


class PurchaseWriteResultUnknown(PurchaseFlowHalted):
    pass


class PurchaseOrderCreatedPending(PurchaseFlowHalted):
    pass


class PurchaseCoolingDown(PurchaseFlowHalted):
    pass


def _mark_committed(
    exc: BaseException,
    amount: float,
    *,
    orders: int = 1,
) -> BaseException:
    """Attach durable-commit metadata to a terminal post-commit failure."""

    exc.committed_amount = max(0.0, float(amount or 0.0))
    exc.committed_orders = max(0, int(orders or 0))
    return exc


@dataclass(frozen=True)
class _PurchaseAttempt:
    status: _PurchaseAttemptStatus
    amount: Optional[float] = None
    reason: str = ""
    order_id: str = ""
    batch_id: str = ""


TARGET_REACHED = object()


SKIP_VERIFICATION_FAILED = object()


def _classify_failure(result: Optional[Dict[str, Any]]) -> _PurchaseAttempt:
    if not result:
        return _PurchaseAttempt(
            _PurchaseAttemptStatus.UNKNOWN_AFTER_SEND, reason="写请求无响应，服务端结果未知"
        )
    code = str(result.get("code") or "").strip().lower()
    message = str(result.get("msg") or result.get("error") or "").strip().lower()
    failure_text = f"{code} {message}"
    batch_id = str(result.get("batch_id") or "")
    order_id = str(result.get("order_id") or "")
    if code in {"unknown_after_send", "write_result_unknown", "unknown"} or (
        "created" in result and result.get("created") is None
    ):
        return _PurchaseAttempt(
            _PurchaseAttemptStatus.UNKNOWN_AFTER_SEND,
            reason=message or code or "写请求结果未知",
            order_id=order_id,
            batch_id=batch_id,
        )
    if code in {"created_without_pay_url", "created_pending"} or result.get("created") is True:
        return _PurchaseAttempt(
            _PurchaseAttemptStatus.CREATED_NOT_PAID,
            reason=message or code or "订单已创建但支付状态未完成",
            order_id=order_id,
            batch_id=batch_id,
        )
    if "cooling_down" in code or "cooling down" in failure_text or "冷却" in failure_text:
        return _PurchaseAttempt(
            _PurchaseAttemptStatus.COOLING_DOWN, reason=message or code or "Buff 处于冷却状态"
        )
    if any(
        (
            token in failure_text
            for token in ("risk", "captcha", "verify", "verification", "风控", "验证")
        )
    ):
        return _PurchaseAttempt(
            _PurchaseAttemptStatus.RISK, reason=message or code or "Buff 返回风控/验证状态"
        )
    safe_fallback_codes = {"unsupported", "not_supported", "batch_unsupported", "not_created"}
    if code in safe_fallback_codes and result.get("created") is False:
        return _PurchaseAttempt(_PurchaseAttemptStatus.SAFE_TO_FALLBACK, reason=message or code)
    if result.get("created") is False:
        return _PurchaseAttempt(
            _PurchaseAttemptStatus.FAILED, reason=message or code or "购买请求被明确拒绝"
        )
    return _PurchaseAttempt(
        _PurchaseAttemptStatus.UNKNOWN_AFTER_SEND,
        reason=message or code or "购买接口返回未识别状态，结果未知",
        order_id=order_id,
        batch_id=batch_id,
    )


def _finish_attempt(attempt: _PurchaseAttempt) -> Optional[float]:
    if attempt.status is _PurchaseAttemptStatus.SUCCESS:
        return attempt.amount
    if attempt.status is _PurchaseAttemptStatus.UNKNOWN_AFTER_SEND:
        raise PurchaseWriteResultUnknown(
            attempt.reason or "BUFF 写请求结果未知，已停止后续购买",
            order_id=attempt.order_id,
            batch_id=attempt.batch_id,
        )
    if attempt.status is _PurchaseAttemptStatus.CREATED_NOT_PAID:
        raise PurchaseOrderCreatedPending(
            attempt.reason or "BUFF 订单已创建但未完成，已停止后续购买",
            order_id=attempt.order_id,
            batch_id=attempt.batch_id,
        )
    if attempt.status is _PurchaseAttemptStatus.COOLING_DOWN:
        raise PurchaseCoolingDown(attempt.reason or "Buff Cooling Down，已停止后续购买请求")
    if attempt.status is _PurchaseAttemptStatus.RISK:
        raise BuffVerificationRequired(
            attempt.reason or "Buff 返回风控/验证状态，已停止后续购买请求"
        )
    if attempt.status is _PurchaseAttemptStatus.TIME_WINDOW_CLOSED:
        return TIME_WINDOW_CLOSED
    return None


@dataclass(frozen=True)
class PurchaseContext:
    _time_window_is_open: Any
    _write_snapshot_is_fresh: Any
    acc: Any
    append_purchase: Any
    buff_cfg: Any
    buff_client: Any
    config: Any
    confirm_payment: Any
    game_buff: Any
    goods_id: Any
    is_stop_requested: Any
    item: Any
    log_fn: Any
    lowest_price: Any
    num_to_buy: Any
    on_entering_payment: Any
    orders: Any
    ref_price: Any
    set_pending_payment: Any
    wait_payment_confirm: Any
