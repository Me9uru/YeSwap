"""Purchase identity responsibility."""

from __future__ import annotations
from typing import Any, Dict
from app.services.buff_checkout_guard import get_unresolved_checkout, update_checkout
from app.purchase.models import (
    PurchaseOrderCreatedPending,
)


def _checkout_credential_identity(buff_client: Any) -> Dict[str, Any]:
    getter = getattr(buff_client, "get_credential_identity", None)
    if callable(getter):
        try:
            value = getter()
            if isinstance(value, dict):
                return value
        except Exception:
            pass
    try:
        generation = int(getattr(buff_client, "_credential_generation", 0) or 0)
    except (TypeError, ValueError):
        generation = 0
    return {
        "credential_generation": generation,
        "credential_fingerprint": "",
    }


def _ensure_checkout_identity_unchanged(buff_client: Any) -> None:
    guard = get_unresolved_checkout() or {}
    expected = str(guard.get("credential_fingerprint") or "")
    current = _checkout_credential_identity(buff_client)
    actual = str(current.get("credential_fingerprint") or "")
    if expected and actual and expected != actual:
        update_checkout(
            expected_intent_id=str(guard.get("intent_id") or "") or None,
            stage="credentials_changed",
            reason="checkout 期间 BUFF 凭证发生变化，禁止继续核销",
            credential_generation=current.get("credential_generation", 0),
            credential_fingerprint=actual,
        )
        raise PurchaseOrderCreatedPending(
            "checkout 期间 BUFF 凭证发生变化，需先人工对账",
            order_id=str(guard.get("order_id") or ""),
            batch_id=str(guard.get("batch_id") or ""),
        )
