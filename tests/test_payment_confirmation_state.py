from app.runtime.state import State


def test_confirmation_is_scoped_to_the_expected_order():
    state = State()
    state.set_pending_payment({"order_id": "new-order"})

    assert state.confirm_payment(False, expected_order_id="old-order") is False
    # 被拒绝的确认不得留下任何决定：等待必须立即返回 False。
    assert state.wait_payment_confirm(timeout_seconds=0) is False
    # 在进入等待之前落下的确认必须保留，并为正确的订单生效。
    assert state.confirm_payment(True, expected_order_id="new-order") is True
    assert state.wait_payment_confirm(timeout_seconds=0) is True
