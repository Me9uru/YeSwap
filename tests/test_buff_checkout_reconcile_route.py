import pytest
from fastapi import HTTPException

from app.routes import pipeline as route


def _blocker(intent_id="intent-1"):
    return {
        "code": "BUFF_RECONCILIATION_REQUIRED",
        "checkout": {"intent_id": intent_id, "order_id": "order-1"},
    }


def test_manual_reconcile_requires_matching_intent(monkeypatch):
    monkeypatch.setattr(route, "get_pipeline_start_blocker", lambda: _blocker())

    with pytest.raises(HTTPException) as exc_info:
        route.api_reconcile_buff_checkout(
            route.CheckoutReconcileBody(
                intent_id="stale-intent",
                outcome="cancelled_or_unpaid",
            )
        )

    assert exc_info.value.status_code == 409


def test_manual_reconcile_acknowledges_exact_checkout(monkeypatch):
    calls = []
    monkeypatch.setattr(route, "get_pipeline_start_blocker", lambda: _blocker())
    monkeypatch.setattr(
        route,
        "acknowledge_checkout",
        lambda intent_id, reason: calls.append((intent_id, reason)),
    )
    monkeypatch.setattr(route, "log", lambda *_args, **_kwargs: None)

    result = route.api_reconcile_buff_checkout(
        route.CheckoutReconcileBody(
            intent_id="intent-1",
            outcome="cancelled_or_unpaid",
        )
    )

    assert result == {"ok": True}
    assert calls[0][0] == "intent-1"


@pytest.mark.parametrize(
    ("step", "expected_status", "expected_step", "expected_safe"),
    [
        # 可随人工核对解除的对账步骤：核对成功后必须复位运行状态并放行
        # 后台 BUFF 读取（收货/库存），否则门禁被已核对的 step 永久挡住
        pytest.param(
            "BUFF_WRITE_RESULT_UNKNOWN",
            "stopped",
            "已人工对账，等待启动",
            True,
            id="write_result_unknown_released",
        ),
        pytest.param(
            "BUFF_ORDER_CREATED_PENDING",
            "stopped",
            "已人工对账，等待启动",
            True,
            id="order_created_pending_released",
        ),
        # 冷却不属于核对可解除的范畴：状态与后台门禁都必须保持
        pytest.param(
            "BUFF_COOLING_DOWN",
            "error",
            "BUFF_COOLING_DOWN",
            False,
            id="cooling_down_kept",
        ),
    ],
)
def test_manual_reconcile_releases_only_reconcilable_steps(
    step, expected_status, expected_step, expected_safe
):
    from app.background import safety
    from app.runtime import state as runtime_state
    from app.services import buff_checkout_guard as guard

    intent = guard.begin_checkout(
        "single", 769139, sell_order_id="sell-1", price=4.77
    )
    runtime_state.set_status("error", step)
    assert safety._buff_background_request_is_safe() is False

    result = route.api_reconcile_buff_checkout(
        route.CheckoutReconcileBody(
            intent_id=intent["intent_id"],
            outcome="cancelled_or_unpaid",
        )
    )

    assert result == {"ok": True}
    assert guard.get_unresolved_checkout() is None
    status = runtime_state.get_status()
    assert status["status"] == expected_status
    assert status["step"] == expected_step
    assert safety._buff_background_request_is_safe() is expected_safe
