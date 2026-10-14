"""Purchase settlement responsibility."""

from __future__ import annotations
import time
from typing import Any, Callable, Dict, List, Optional
from app.configuration.repository import get_steam_credentials
from app.services.buff_checkout_guard import resolve_checkout, update_checkout
from buff import BuffAuthExpired, BuffRequestBlocked, BuffWriteResultUnknown
from app.purchase.models import (
    PurchaseOrderCreatedPending,
    PurchaseWriteResultUnknown,
    _mark_committed,
)
from app.purchase.payment import (
    _do_payment_notify_and_wait,
    _supports_keyword_argument,
)
from app.purchase.identity import (
    _ensure_checkout_identity_unchanged,
)
from app.purchase.market import (
    _fetch_smart_market_price,
)


def _validate_unique_batch_matches(
    matches: Any,
    batch_id: str,
) -> List[Dict[str, Any]]:
    """Reject malformed/duplicate IDs before treating a batch as complete."""

    valid: List[Dict[str, Any]] = []
    seen_bill_ids = set()
    seen_sell_ids = set()
    invalid = not isinstance(matches, list)
    for match in matches if isinstance(matches, list) else []:
        if not isinstance(match, dict):
            invalid = True
            continue
        bill_id = str(match.get("bill_order_id") or "").strip()
        sell_id = str(match.get("id") or "").strip()
        if (
            not bill_id
            or bill_id == "0"
            or not sell_id
            or sell_id == "0"
            or bill_id in seen_bill_ids
            or sell_id in seen_sell_ids
        ):
            invalid = True
            continue
        seen_bill_ids.add(bill_id)
        seen_sell_ids.add(sell_id)
        normalized = dict(match)
        normalized["bill_order_id"] = bill_id
        normalized["id"] = sell_id
        valid.append(normalized)

    if invalid or len(valid) != len(matches):
        error = BuffWriteResultUnknown(
            "BUFF 批量核销结果包含空值或重复订单号，无法确认完整件数",
            method="POST",
        )
        error.partial_results = valid
        error.batch_id = str(batch_id)
        raise error
    return valid


def _do_batch_wait_finalize_and_append(
    buff_client: Any,
    item: Dict[str, Any],
    config: dict,
    unit_price: float,
    num: int,
    goods_id: int,
    batch_id: str,
    checkout_intent_id: str,
    game_buff: str,
    pay_url: str,
    set_pending_payment: callable,
    wait_payment_confirm: callable,
    confirm_payment: callable,
    is_stop_requested: callable,
    append_purchase: callable,
    log_fn: Optional[Callable[[str, str], None]],
    market_price: Optional[float] = None,
    on_entering_payment: Optional[Callable[[], None]] = None,
) -> Optional[float]:
    ok = _do_payment_notify_and_wait(
        item,
        config,
        pay_url,
        "wechat",
        batch_id,
        set_pending_payment,
        wait_payment_confirm,
        confirm_payment,
        is_stop_requested,
        log_fn,
        on_entering_payment,
    )
    if is_stop_requested() or not ok:
        update_checkout(
            expected_intent_id=checkout_intent_id,
            stage="batch_created_pending",
            batch_id=str(batch_id),
            reason="用户取消、超时或付款状态未确认",
        )
        return None
    if log_fn:
        log_fn("[Buff]   → 正在扫描市场匹配卖家并核销…", "info")
    _ensure_checkout_identity_unchanged(buff_client)
    update_checkout(
        expected_intent_id=checkout_intent_id,
        stage="batch_finalizing",
        batch_id=str(batch_id),
        reason="付款已确认，正在核销卖单",
    )
    if market_price is None:
        mhn = (item.get("steam_market_name") or item.get("name") or "").strip()
        market_price = _fetch_smart_market_price(mhn, config, app_id=730)
    saved_name = (item.get("steam_market_name") or item.get("name") or "").strip()

    def _persist_matches(matches: List[Dict[str, Any]]) -> float:
        subtotal = 0.0
        for match in matches:
            price = float(match.get("price", 0) or 0)
            subtotal += price
            bill_order_id = str(match.get("bill_order_id") or "")
            rec = {
                "name": saved_name,
                "goods_id": goods_id,
                "price": price,
                "at": time.time(),
                "pending_receipt": True,
                "batch_id": str(batch_id),
                "buff_order_id": bill_order_id,
                "bill_order_id": bill_order_id,
                "buff_sell_order_id": str(match.get("id") or ""),
            }
            if market_price is not None and market_price > 0:
                rec["market_price"] = round(float(market_price), 2)
            append_purchase(rec)
        return subtotal

    def _on_match(
        _match: Dict[str, Any],
        matches: List[Dict[str, Any]],
    ) -> None:
        completed_ids = [
            str(match.get("bill_order_id") or "") for match in matches if match.get("bill_order_id")
        ]
        update_checkout(
            expected_intent_id=checkout_intent_id,
            stage="batch_finalizing",
            batch_id=str(batch_id),
            partial_results=matches,
            completed_order_ids=completed_ids,
            reason=f"已取得 {len(matches)} 个 BUFF 核销结果，准备下一次核销",
        )

    try:
        finalize = buff_client.batch_buy_find_and_finalize
        if _supports_keyword_argument(finalize, "on_match"):
            matched = finalize(
                goods_id,
                game_buff,
                unit_price,
                num,
                batch_id,
                on_match=_on_match,
            )
        else:
            matched = finalize(
                goods_id,
                game_buff,
                unit_price,
                num,
                batch_id,
            )
        matched = _validate_unique_batch_matches(matched, batch_id)
    except Exception as exc:
        partial = getattr(exc, "partial_results", None)
        partial = partial if isinstance(partial, list) else []
        completed_ids = [
            str(match.get("bill_order_id") or "") for match in partial if match.get("bill_order_id")
        ]
        # Persist external identifiers before any local row write.  If a later
        # DB commit or process crashes, reconciliation still has every BUFF id.
        update_checkout(
            expected_intent_id=checkout_intent_id,
            stage="batch_finalize_unknown",
            batch_id=str(batch_id),
            partial_results=partial,
            completed_order_ids=completed_ids,
            reason=str(exc) or type(exc).__name__,
            last_error_type=type(exc).__name__,
        )
        committed_total = _persist_matches(partial) if partial else 0.0
        if isinstance(exc, BuffWriteResultUnknown):
            _mark_committed(exc, committed_total, orders=len(partial))
            raise
        if isinstance(exc, (BuffAuthExpired, BuffRequestBlocked)):
            pending = PurchaseOrderCreatedPending(
                "批次已创建，核销时认证/请求策略中断，需人工对账",
                batch_id=str(batch_id),
            )
            _mark_committed(pending, committed_total, orders=len(partial))
            raise pending from exc
        unknown = BuffWriteResultUnknown(
            "批量核销异常，结果未知，禁止继续购买",
            method="POST",
        )
        _mark_committed(unknown, committed_total, orders=len(partial))
        raise unknown from exc

    if not matched:
        update_checkout(
            expected_intent_id=checkout_intent_id,
            stage="batch_created_pending",
            batch_id=str(batch_id),
            reason="付款后未核销到可确认的订单",
        )
        if log_fn:
            log_fn("[Buff]   → 未找到可确认的核销结果，已停止并等待人工对账", "warn")
        return None
    if log_fn:
        log_fn(f"[Buff]   → 核销成功 {len(matched)} 件", "info")
    completed_ids = [
        str(match.get("bill_order_id") or "") for match in matched if match.get("bill_order_id")
    ]
    update_checkout(
        expected_intent_id=checkout_intent_id,
        stage="batch_matches_received",
        batch_id=str(batch_id),
        completed_order_ids=completed_ids,
        partial_results=matched,
        reason=f"已取得 {len(matched)} 个 BUFF 核销结果，正在写入本地记录",
    )
    total = _persist_matches(matched)
    if len(matched) < num:
        update_checkout(
            expected_intent_id=checkout_intent_id,
            stage="batch_partial",
            batch_id=str(batch_id),
            completed_order_ids=completed_ids,
            partial_results=matched,
            reason=f"仅核销 {len(matched)}/{num} 件",
        )
        if log_fn:
            log_fn(
                f"[Buff]   → 批量核销仅完成 {len(matched)}/{num} 件；已记录成功部分并停止后续购买",
                "warn",
            )
        pending = PurchaseOrderCreatedPending(
            f"Buff 批次仅核销 {len(matched)}/{num} 件，剩余状态需人工对账",
            batch_id=str(batch_id),
        )
        _mark_committed(pending, total, orders=len(matched))
        raise pending
    update_checkout(
        expected_intent_id=checkout_intent_id,
        stage="purchase_recorded",
        batch_id=str(batch_id),
        completed_order_ids=completed_ids,
        reason="全部核销结果已写入本地交易记录",
    )
    bill_order_ids = [m.get("bill_order_id") for m in matched if m.get("bill_order_id")]
    auto_ask_seller = bool((config.get("buff") or {}).get("auto_ask_seller_to_send", False))
    if auto_ask_seller and bill_order_ids:
        try:
            if buff_client.ask_seller_to_send(bill_order_ids, game_buff) and log_fn:
                log_fn(
                    f"[Buff]   → 已逐单提醒 {len(set(bill_order_ids))} 个卖家发货，请留意 Steam 报价",
                    "info",
                )
            elif log_fn:
                log_fn("[Buff]   → 提醒卖家发货未成功（可稍后在订单页手动催发货）", "warn")
        except (BuffAuthExpired, BuffRequestBlocked) as exc:
            resolve_checkout(
                "batch_purchase_recorded_shipping_prompt_blocked",
                expected_intent_id=checkout_intent_id,
            )
            _mark_committed(exc, total, orders=len(matched))
            raise
        except Exception as exc:
            update_checkout(
                expected_intent_id=checkout_intent_id,
                stage="shipping_reminder_unknown",
                batch_id=str(batch_id),
                completed_order_ids=completed_ids,
                reason=f"{type(exc).__name__}: {exc}",
                last_error_type=type(exc).__name__,
            )
            if log_fn:
                log_fn(
                    f"[Buff]   → 成交已记录；提醒卖家发货结果未知 ({type(exc).__name__})，已停止后续 BUFF 写请求",
                    "warn",
                )
            halted = PurchaseWriteResultUnknown(
                "成交已记录，但提醒卖家发货的写请求结果未知",
                batch_id=str(batch_id),
            )
            _mark_committed(halted, total, orders=len(matched))
            raise halted from exc
    elif log_fn:
        log_fn("[Buff]   → 已跳过自动催发货，以减少风控敏感写请求", "info")
    resolve_checkout(
        "batch_purchase_recorded",
        expected_intent_id=checkout_intent_id,
    )
    return total


def _do_wait_payment_and_append(
    buff_client: Any,
    item: Dict[str, Any],
    config: dict,
    unit_price: float,
    num: int,
    goods_id: int,
    pay_url: str,
    pay_type: str,
    order_id: str,
    checkout_intent_id: str,
    set_pending_payment: callable,
    wait_payment_confirm: callable,
    confirm_payment: callable,
    is_stop_requested: callable,
    append_purchase: callable,
    log_fn: Optional[Callable[[str, str], None]],
    game_buff: str,
    sell_order_id: str = "",
    market_price: Optional[float] = None,
    on_entering_payment: Optional[Callable[[], None]] = None,
) -> Optional[float]:
    ok = _do_payment_notify_and_wait(
        item,
        config,
        pay_url,
        pay_type,
        order_id,
        set_pending_payment,
        wait_payment_confirm,
        confirm_payment,
        is_stop_requested,
        log_fn,
        on_entering_payment,
    )
    if is_stop_requested() or not ok:
        return None
    return _append_paid_single_order(
        buff_client,
        item,
        config,
        unit_price,
        num,
        goods_id,
        order_id,
        checkout_intent_id,
        append_purchase,
        log_fn,
        game_buff,
        sell_order_id=sell_order_id,
        market_price=market_price,
    )


def _append_paid_single_order(
    buff_client: Any,
    item: Dict[str, Any],
    config: dict,
    unit_price: float,
    num: int,
    goods_id: int,
    order_id: str,
    checkout_intent_id: str,
    append_purchase: callable,
    log_fn: Optional[Callable[[str, str], None]],
    game_buff: str,
    sell_order_id: str = "",
    market_price: Optional[float] = None,
) -> float:
    """落库一笔已确认支付的单件订单并结束对账门禁。

    人工确认付款与免密自动扣款两条路径共用；调用方必须已确认订单已支付。
    """

    if market_price is None:
        mhn = (item.get("steam_market_name") or item.get("name") or "").strip()
        market_price = _fetch_smart_market_price(mhn, config, app_id=730)
    saved_name = (item.get("steam_market_name") or item.get("name") or "").strip()
    base_rec = {
        "name": saved_name,
        "goods_id": goods_id,
        "price": unit_price,
        "at": time.time(),
        "pending_receipt": True,
        "buff_order_id": str(order_id),
        "buff_sell_order_id": str(sell_order_id),
    }
    if market_price is not None and market_price > 0:
        base_rec["market_price"] = round(float(market_price), 2)
    for _ in range(num):
        append_purchase(dict(base_rec))
    update_checkout(
        expected_intent_id=checkout_intent_id,
        stage="purchase_recorded",
        order_id=str(order_id),
        reason="订单已写入本地交易记录",
    )
    sent_buyer_offer = False
    if bool((config.get("buff") or {}).get("auto_send_buyer_offer", False)):
        sent_buyer_offer = _maybe_send_buyer_offer(
            buff_client,
            order_id,
            game_buff,
            checkout_intent_id,
            unit_price * num,
            log_fn,
        )
    auto_ask_seller = bool((config.get("buff") or {}).get("auto_ask_seller_to_send", False))
    if auto_ask_seller and not sent_buyer_offer:
        try:
            if buff_client.ask_seller_to_send(order_id, game_buff) and log_fn:
                log_fn("[Buff]   → 已提醒卖家发货，请留意 Steam 报价", "info")
            elif log_fn:
                log_fn("[Buff]   → 提醒卖家发货未成功（可稍后在订单页手动催发货）", "warn")
        except (BuffAuthExpired, BuffRequestBlocked) as exc:
            resolve_checkout(
                "single_purchase_recorded_shipping_prompt_blocked",
                expected_intent_id=checkout_intent_id,
            )
            _mark_committed(exc, unit_price * num)
            raise
        except Exception as exc:
            update_checkout(
                expected_intent_id=checkout_intent_id,
                stage="shipping_reminder_unknown",
                order_id=str(order_id),
                reason=f"{type(exc).__name__}: {exc}",
                last_error_type=type(exc).__name__,
            )
            if log_fn:
                log_fn(
                    f"[Buff]   → 成交已记录；提醒卖家发货结果未知 ({type(exc).__name__})，已停止后续 BUFF 写请求",
                    "warn",
                )
            halted = PurchaseWriteResultUnknown(
                "成交已记录，但提醒卖家发货的写请求结果未知",
                order_id=str(order_id),
            )
            _mark_committed(halted, unit_price * num)
            raise halted from exc
    elif log_fn and not sent_buyer_offer:
        log_fn("[Buff]   → 已跳过自动催发货，以减少风控敏感写请求", "info")
    resolve_checkout(
        "single_purchase_recorded",
        expected_intent_id=checkout_intent_id,
    )
    return unit_price * num


def _steam_cookies_string(raw: Any) -> str:
    if isinstance(raw, dict):
        return "; ".join(f"{key}={value}" for key, value in raw.items() if key)
    return str(raw or "").strip()


def _maybe_send_buyer_offer(
    buff_client: Any,
    order_id: str,
    game_buff: str,
    checkout_intent_id: str,
    committed_amount: float,
    log_fn: Optional[Callable[[str, str], None]],
) -> bool:
    """手动+ 订单：自动以买家身份发起 Steam 报价（BUFF 服务端代发）。

    只有模式 5/8 且尚未发起报价的订单会尝试：把（加密的）Steam Cookie 交给
    BUFF，由 BUFF 用该会话向卖家发起交易报价。返回 True 仅当报价已确认发起，
    此时不再重复提醒卖家；未适用或 BUFF 明确拒绝时返回 False，调用方可回退到
    提醒卖家。写请求结果未知时保持保守门禁：成交已记账但立即停止后续 BUFF
    写请求。
    """

    try:
        state = buff_client.get_bill_order_pay_state(str(order_id), game_buff)
    except Exception as exc:
        if log_fn:
            log_fn(
                f"[Buff]   → 查询订单状态失败，跳过自动发起报价 ({type(exc).__name__})",
                "warn",
            )
        return False
    if not isinstance(state, dict):
        return False
    if state.get("mode") not in (5, 8) or state.get("has_sent_offer"):
        return False
    cookies = _steam_cookies_string((get_steam_credentials() or {}).get("cookies"))
    if not cookies:
        if log_fn:
            log_fn(
                "[Buff]   → 该订单需要买家发起报价，但未配置 Steam 凭证，请在 APP 手动发起",
                "warn",
            )
        return False
    try:
        result = buff_client.buyer_send_offer(cookies, [str(order_id)])
    except (BuffAuthExpired, BuffRequestBlocked) as exc:
        resolve_checkout(
            "single_purchase_recorded_buyer_offer_blocked",
            expected_intent_id=checkout_intent_id,
        )
        _mark_committed(exc, committed_amount)
        raise
    except Exception as exc:
        update_checkout(
            expected_intent_id=checkout_intent_id,
            stage="buyer_offer_unknown",
            order_id=str(order_id),
            reason=f"{type(exc).__name__}: {exc}",
            last_error_type=type(exc).__name__,
        )
        if log_fn:
            log_fn(
                f"[Buff]   → 成交已记录；自动发起报价结果未知 ({type(exc).__name__})，已停止后续 BUFF 写请求",
                "warn",
            )
        halted = PurchaseWriteResultUnknown(
            "成交已记录，但自动发起报价的写请求结果未知",
            order_id=str(order_id),
        )
        _mark_committed(halted, committed_amount)
        raise halted from exc
    if isinstance(result, dict) and result.get("success"):
        if log_fn:
            log_fn("[Buff]   → 已自动发起 Steam 报价（BUFF 代发），等待卖家回应", "info")
        return True
    error = result.get("error") if isinstance(result, dict) else "响应异常"
    if log_fn:
        log_fn(
            f"[Buff]   → 自动发起报价失败（{error or '未知原因'}），可稍后在 APP 手动发起",
            "warn",
        )
    return False
