"""Safety."""

from app.runtime.state import get_status


_BUFF_RECONCILIATION_REQUIRED_STEPS = frozenset(
    {
        "BUFF_WRITE_RESULT_UNKNOWN",
        "BUFF_ORDER_CREATED_PENDING",
        "BUFF_COOLING_DOWN",
        "PIPELINE_UNEXPECTED_ERROR",
    }
)


def _buff_background_request_is_safe() -> bool:
    """BUFF background reads must never overlap a buy pipeline or checkout."""

    from app.runtime.pipeline_control import is_shutdown_pending
    from app.services.buff_checkout_guard import get_unresolved_checkout

    if is_shutdown_pending() or get_unresolved_checkout() is not None:
        return False
    status = get_status() or {}
    return (
        status.get("status") != "running"
        and status.get("step") != "CHECKOUT_PENDING"
        and status.get("step") not in _BUFF_RECONCILIATION_REQUIRED_STEPS
        and not status.get("buff_auth_expired")
        and not status.get("buff_verification_required")
    )


def _session_keepalive_is_safe() -> bool:
    """Never open an auth browser while a pipeline or checkout is active."""

    from app.runtime.pipeline_control import is_shutdown_pending
    from app.services.buff_checkout_guard import get_unresolved_checkout

    if is_shutdown_pending() or get_unresolved_checkout() is not None:
        return False
    status = get_status() or {}
    return (
        status.get("status") != "running"
        and status.get("step") != "CHECKOUT_PENDING"
        and status.get("step") not in _BUFF_RECONCILIATION_REQUIRED_STEPS
    )
