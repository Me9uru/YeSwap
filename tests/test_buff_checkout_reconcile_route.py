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
    assert calls == [("intent-1", "user_confirmed_cancelled_or_unpaid")]
