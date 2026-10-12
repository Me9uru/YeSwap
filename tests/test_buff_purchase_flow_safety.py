from app.purchase import checkout as purchase_checkout
from app.purchase import market as purchase_market
from app.purchase import settlement as purchase_settlement
import time

import pytest

from app.purchase import checkout, models
from app.workflows import purchase as pipeline_module
from buff import BuffWriteResultUnknown
from buff.request_policy import BuffRateLimited


def _config(pay_method="alipay"):
    return {
        "buff": {
            "game": "csgo",
            "pay_method": pay_method,
            "price_tolerance": 0.5,
        },
        "pipeline": {"buff_sell_orders_cache_ttl_seconds": 3},
        "_strategy_runtime": {"buy": {"enabled_modules": []}},
    }


def _item(orders, fetched_at=None):
    return {
        "name": "Test Item",
        "steam_market_name": "Test Item",
        "goods_id": 123,
        "min_price": 10.0,
        "daily_volume": 100,
        "_buff_lowest_price": 10.0,
        "_buff_sell_orders": orders,
        "_buff_sell_orders_fetched_at": time.time() if fetched_at is None else fetched_at,
    }


def _checkout_args(wait_result=False):
    pending = []
    purchases = []
    return {
        "target_balance": 100.0,
        "acc": 0.0,
        "set_pending_payment": pending.append,
        "wait_payment_confirm": lambda **_kwargs: wait_result,
        "confirm_payment": lambda _ok: None,
        "is_stop_requested": lambda: False,
        "append_purchase": purchases.append,
    }, pending, purchases


class _FakeState:
    def __init__(self, *, payment_confirmed=False):
        self.payment_confirmed = payment_confirmed
        self.pending = None
        self.purchases = []
        self.status = ("idle", "")
        self.logs = []

    def clear_stop(self):
        return None

    def is_stop_requested(self):
        return False

    def set_buff_auth_expired(self, _value):
        return None

    def set_buff_verification_required(self, _value, _reason=""):
        return None

    def set_status(self, status, step="", **_kwargs):
        self.status = (status, step)

    def log(self, msg, level="info", category="", flow_id=""):
        self.logs.append((msg, level, category, flow_id))

    def set_pending_payment(self, value):
        self.pending = value

    def wait_payment_confirm(self, **_kwargs):
        return self.payment_confirmed

    def confirm_payment(self, value):
        self.payment_confirmed = bool(value)

    def append_purchase(self, value):
        self.purchases.append(value)


class _FakeContext:
    def __init__(self, state):
        self.state = state
        self.verbose = False

    def is_stop_requested(self):
        return self.state.is_stop_requested()

    def log(self, msg, level="info", category="pipeline"):
        self.state.log(msg, level, category=category)

    def set_status(self, status, step="", **kwargs):
        self.state.set_status(status, step, **kwargs)

    def debug(self, _msg, category="pipeline"):
        return None


def test_closed_time_window_blocks_checkout_post_after_validation():
    class BuffClient:
        _pay_method = "alipay"

        def __init__(self):
            self.lock_calls = 0

        def lock_and_get_pay_url(self, *_args):
            self.lock_calls += 1
            raise AssertionError("checkout POST must not be called")

    client = BuffClient()
    kwargs, _pending, _purchases = _checkout_args()
    window_checks = iter([True, False])

    result = checkout.lock_and_confirm_payment(
        client,
        _item([{"id": "sell-1", "price": "10.0"}]),
        _config(),
        is_time_allowed=lambda: next(window_checks),
        **kwargs,
    )

    from app.services.buff_checkout_guard import get_unresolved_checkout

    assert result is models.TIME_WINDOW_CLOSED
    assert client.lock_calls == 0
    assert get_unresolved_checkout() is None


@pytest.mark.parametrize(
    ("window_checks", "expected"),
    [
        # 候选分析期间关窗：一次锁单请求都不能发出
        pytest.param([True, False], (0.0, 0, []), id="during_candidate_analysis"),
        # 首件成交后关窗：只保留已经发出并成交的那一件
        pytest.param([True, True, False], (10.0, 1, [True]), id="after_first_purchase"),
    ],
)
def test_time_window_closing_stops_further_checkout(
    monkeypatch, window_checks, expected
):
    expected_acc, expected_bought, expected_checkouts = expected
    item = _item([{"id": "sell-1", "price": "10.0"}])
    state = _FakeState()
    ctx = _FakeContext(state)
    checkout_calls = []
    checks = iter(window_checks)

    monkeypatch.setattr(
        pipeline_module,
        "pick_stable_item",
        lambda *_args, **_kwargs: (item, set()),
    )

    def checkout(*_args, **_kwargs):
        checkout_calls.append(True)
        return 10.0

    monkeypatch.setattr(
        pipeline_module,
        "lock_and_confirm_payment",
        checkout,
    )

    acc, bought, stopped = pipeline_module._process_deals_for_target(
        ctx,
        [item],
        _config(),
        30.0,
        0.0,
        0,
        object(),
        object(),
        object(),
        set(),
        set(),
        set(),
        is_time_allowed=lambda: next(checks),
    )

    assert (acc, bought) == (expected_acc, expected_bought)
    assert stopped is models.TIME_WINDOW_CLOSED
    assert checkout_calls == expected_checkouts


def test_stale_buff_cache_is_refreshed_before_locking():
    class BuffClient:
        _pay_method = "alipay"

        def __init__(self):
            self.get_calls = 0
            self.locked_order_ids = []

        def get_sell_orders(self, _goods_id, _game):
            self.get_calls += 1
            return [{"id": "new-order", "price": "10.0"}]

        def lock_and_get_pay_url(self, _game, _goods_id, order_id, _price):
            self.locked_order_ids.append(order_id)
            return {
                "success": False,
                "code": "FAIL",
                "msg": "sold",
                "created": False,
            }

    client = BuffClient()
    item = _item(
        [{"id": "stale-order", "price": "10.0"}],
        fetched_at=time.time() - 10,
    )
    kwargs, _pending, _purchases = _checkout_args()

    result = checkout.lock_and_confirm_payment(client, item, _config(), **kwargs)

    assert result is None
    assert client.get_calls == 1
    assert client.locked_order_ids == ["new-order"]
    assert item["_buff_sell_orders"][0]["id"] == "new-order"


def test_single_order_payment_cancel_does_not_lock_again():
    class BuffClient:
        _pay_method = "alipay"

        def __init__(self):
            self.lock_calls = 0

        def get_sell_orders(self, _goods_id, _game):
            raise AssertionError("fresh cache should be reused")

        def lock_and_get_pay_url(self, _game, _goods_id, _order_id, _price):
            self.lock_calls += 1
            return {
                "success": True,
                "order_id": "bill-1",
                "pay_url": "https://pay.invalid/1",
                "pay_type": "alipay",
            }

    client = BuffClient()
    kwargs, pending, purchases = _checkout_args(wait_result=False)

    with pytest.raises(models.PurchaseOrderCreatedPending) as exc_info:
        checkout.lock_and_confirm_payment(
            client,
            _item([{"id": "sell-1", "price": "10.0"}]),
            _config(),
            **kwargs,
        )

    assert exc_info.value.order_id == "bill-1"
    assert client.lock_calls == 1
    assert pending[-1] is None
    assert purchases == []


def test_rate_limit_is_not_swallowed_or_retried():
    class BuffClient:
        _pay_method = "alipay"

        def __init__(self):
            self.lock_calls = 0

        def get_sell_orders(self, _goods_id, _game):
            raise AssertionError("fresh cache should be reused")

        def lock_and_get_pay_url(self, *_args):
            self.lock_calls += 1
            raise BuffRateLimited(30)

    client = BuffClient()
    kwargs, _pending, _purchases = _checkout_args()

    with pytest.raises(BuffRateLimited):
        checkout.lock_and_confirm_payment(
            client,
            _item([{"id": "sell-1", "price": "10.0"}]),
            _config(),
            **kwargs,
        )

    assert client.lock_calls == 1


def test_transport_unknown_write_is_converted_to_terminal_purchase_state():
    class BuffClient:
        _pay_method = "alipay"

        def __init__(self):
            self.lock_calls = 0

        def lock_and_get_pay_url(self, *_args):
            self.lock_calls += 1
            raise BuffWriteResultUnknown(
                "socket closed after POST",
                method="POST",
                url="https://buff.invalid/buy",
            )

    client = BuffClient()
    kwargs, _pending, _purchases = _checkout_args()

    with pytest.raises(models.PurchaseWriteResultUnknown, match="socket closed"):
        checkout.lock_and_confirm_payment(
            client,
            _item([{"id": "sell-1", "price": "10.0"}]),
            _config(),
            **kwargs,
        )

    assert client.lock_calls == 1


def test_batch_payment_cancel_does_not_fallback_to_single_order():
    class BuffClient:
        _pay_method = "wechat"

        def __init__(self):
            self.batch_calls = 0
            self.single_calls = 0
            self.finalize_calls = 0

        def get_sell_orders(self, _goods_id, _game):
            raise AssertionError("fresh cache should be reused")

        def try_batch_buy(self, *_args):
            self.batch_calls += 1
            return {
                "success": True,
                "batch_id": "batch-1",
                "pay_url": "https://pay.invalid/batch-1",
                "total_price": 20.0,
            }

        def lock_and_get_pay_url(self, *_args):
            self.single_calls += 1
            return {"success": False, "code": "FAIL", "created": False}

        def batch_buy_find_and_finalize(self, *_args):
            self.finalize_calls += 1
            return []

    client = BuffClient()
    kwargs, _pending, purchases = _checkout_args(wait_result=False)

    with pytest.raises(models.PurchaseOrderCreatedPending) as exc_info:
        checkout.lock_and_confirm_payment(
            client,
            _item([
                {"id": "sell-1", "price": "10.0"},
                {"id": "sell-2", "price": "10.0"},
            ]),
            _config("wechat"),
            **kwargs,
        )

    assert exc_info.value.batch_id == "batch-1"
    assert client.batch_calls == 1
    assert client.single_calls == 0
    assert client.finalize_calls == 0
    assert purchases == []


def test_unknown_batch_result_does_not_fallback_to_single_order():
    class BuffClient:
        _pay_method = "wechat"

        def __init__(self):
            self.single_calls = 0

        def get_sell_orders(self, _goods_id, _game):
            raise AssertionError("fresh cache should be reused")

        def try_batch_buy(self, *_args):
            return None

        def lock_and_get_pay_url(self, *_args):
            self.single_calls += 1
            return {"success": False, "code": "FAIL", "created": False}

    client = BuffClient()
    kwargs, _pending, _purchases = _checkout_args()

    with pytest.raises(models.PurchaseWriteResultUnknown) as exc_info:
        checkout.lock_and_confirm_payment(
            client,
            _item([
                {"id": "sell-1", "price": "10.0"},
                {"id": "sell-2", "price": "10.0"},
            ]),
            _config("wechat"),
            **kwargs,
        )

    assert client.single_calls == 0


@pytest.mark.parametrize(
    ("pay_method", "batch_response", "expected_batch_calls"),
    [
        # alipay 在发出任何批量写请求之前就已知不支持批量
        pytest.param("alipay", None, 0, id="explicitly_unsupported"),
        # wechat 返回「未创建且可安全降级」，只允许降级一次
        pytest.param(
            "wechat",
            {
                "success": False,
                "code": "NOT_SUPPORTED",
                "created": False,
                "safe_to_fallback": True,
            },
            1,
            id="trusted_not_created",
        ),
    ],
)
def test_batch_fallback_to_single_order_happens_once(
    pay_method, batch_response, expected_batch_calls
):
    class BuffClient:
        _pay_method = pay_method

        def __init__(self):
            self.batch_calls = 0
            self.single_calls = 0

        def get_sell_orders(self, _goods_id, _game):
            raise AssertionError("fresh cache should be reused")

        def try_batch_buy(self, *_args):
            self.batch_calls += 1
            if batch_response is None:
                raise AssertionError("alipay is known not to support batch checkout")
            return batch_response

        def lock_and_get_pay_url(self, *_args):
            self.single_calls += 1
            return {"success": False, "code": "FAIL", "created": False}

    client = BuffClient()
    kwargs, _pending, _purchases = _checkout_args()

    result = checkout.lock_and_confirm_payment(
        client,
        _item([
            {"id": "sell-1", "price": "10.0"},
            {"id": "sell-2", "price": "10.0"},
        ]),
        _config(pay_method),
        **kwargs,
    )

    assert result is None
    assert client.batch_calls == expected_batch_calls
    assert client.single_calls == 1


def test_unknown_write_result_stops_entire_pipeline_with_explicit_status(monkeypatch):
    item = _item([{"id": "sell-1", "price": "10.0"}])
    state = _FakeState()

    class BuffClient:
        _pay_method = "alipay"

        def __init__(self):
            self.lock_calls = 0

        def lock_and_get_pay_url(self, *_args):
            self.lock_calls += 1
            return {
                "success": False,
                "code": "UNKNOWN_AFTER_SEND",
                "created": None,
            }

    class ProxyManager:
        def is_proxy_enabled(self):
            return False

    class NetworkChecker:
        def report_success(self):
            return None

    client = BuffClient()
    monkeypatch.setattr(pipeline_module, "get_state", lambda: state)
    monkeypatch.setattr(
        pipeline_module,
        "apply_strategy_to_config",
        lambda cfg, _kind: {
            **cfg,
            "_strategy_runtime": {"buy": {"enabled_modules": []}},
        },
    )
    monkeypatch.setattr(
        pipeline_module,
        "get_buff_credentials",
        lambda: {"cookies": "session=test"},
    )
    monkeypatch.setattr(
        pipeline_module,
        "create_buff_client_from_config",
        lambda *_args, **_kwargs: client,
    )
    monkeypatch.setattr(pipeline_module, "get_proxy_manager", ProxyManager)
    monkeypatch.setattr(pipeline_module, "get_network_checker", NetworkChecker)
    monkeypatch.setattr(
        pipeline_module,
        "_fetch_and_filter_deals",
        lambda *_args, **_kwargs: ([item], False),
    )
    monkeypatch.setattr(
        pipeline_module,
        "pick_stable_item",
        lambda *_args, **_kwargs: (item, set()),
    )
    monkeypatch.setattr(pipeline_module, "SteamClient", lambda: object())
    monkeypatch.setattr(pipeline_module, "StabilityAnalyzer", lambda **_kwargs: object())

    pipeline_module._run_pipeline({"pipeline": {"target_balance": 100}})

    assert client.lock_calls == 1
    assert state.status == ("error", "BUFF_WRITE_RESULT_UNKNOWN")
    assert any("停止全部后续购买请求" in entry[0] for entry in state.logs)


def test_purchase_amount_is_counted_when_post_commit_shipping_prompt_is_blocked(monkeypatch):
    item = _item([{"id": "sell-1", "price": "10.0"}])
    state = _FakeState(payment_confirmed=True)
    ctx = _FakeContext(state)

    class BuffClient:
        _pay_method = "alipay"

        def lock_and_get_pay_url(self, *_args):
            return {
                "success": True,
                "order_id": "bill-committed",
                "pay_url": "https://pay.invalid/committed",
                "pay_type": "alipay",
            }

        def ask_seller_to_send(self, *_args):
            raise BuffRateLimited(30)

    monkeypatch.setattr(
        pipeline_module,
        "pick_stable_item",
        lambda *_args, **_kwargs: (item, set()),
    )
    monkeypatch.setattr(purchase_settlement, "_fetch_smart_market_price", lambda *_args, **_kwargs: None)
    config = _config()
    config["buff"]["auto_ask_seller_to_send"] = True

    acc, bought, stopped = pipeline_module._process_deals_for_target(
        ctx,
        [item],
        config,
        10.0,
        0.0,
        0,
        object(),
        object(),
        BuffClient(),
        set(),
        set(),
        set(),
    )

    assert (acc, bought, stopped) == (10.0, 1, True)
    assert state.status == ("error", "BUFF_RATE_LIMITED")
    assert len(state.purchases) == 1
    assert state.purchases[0]["buff_order_id"] == "bill-committed"
    assert state.pending is None


def test_successful_batch_records_reconciliation_identifiers(monkeypatch):
    class BuffClient:
        _pay_method = "wechat"

        def try_batch_buy(self, *_args):
            return {
                "success": True,
                "batch_id": "batch-recorded",
                "pay_url": "https://pay.invalid/batch-recorded",
                "total_price": 20.0,
            }

        def batch_buy_find_and_finalize(self, *_args):
            return [
                {"id": "sell-1", "price": 10.0, "bill_order_id": "bill-1"},
                {"id": "sell-2", "price": 10.0, "bill_order_id": "bill-2"},
            ]

        def ask_seller_to_send(self, *_args):
            return True

    kwargs, _pending, purchases = _checkout_args(wait_result=True)
    monkeypatch.setattr(purchase_settlement, "_fetch_smart_market_price", lambda *_args, **_kwargs: None)

    paid = checkout.lock_and_confirm_payment(
        BuffClient(),
        _item([
            {"id": "sell-1", "price": "10.0"},
            {"id": "sell-2", "price": "10.0"},
        ]),
        _config("wechat"),
        **kwargs,
    )

    assert paid == 20.0
    assert [record["bill_order_id"] for record in purchases] == ["bill-1", "bill-2"]
    assert all(record["batch_id"] == "batch-recorded" for record in purchases)
    assert [record["buff_sell_order_id"] for record in purchases] == ["sell-1", "sell-2"]


def test_partial_batch_finalize_records_committed_items_and_halts(monkeypatch):
    class BuffClient:
        _pay_method = "wechat"

        def __init__(self):
            self.shipping_calls = 0

        def try_batch_buy(self, *_args):
            return {
                "success": True,
                "batch_id": "batch-partial",
                "pay_url": "https://pay.invalid/batch-partial",
                "total_price": 20.0,
            }

        def batch_buy_find_and_finalize(self, *_args):
            return [
                {"id": "sell-1", "price": 10.0, "bill_order_id": "bill-1"},
            ]

        def ask_seller_to_send(self, *_args):
            self.shipping_calls += 1
            return True

    client = BuffClient()
    kwargs, _pending, purchases = _checkout_args(wait_result=True)
    monkeypatch.setattr(purchase_settlement, "_fetch_smart_market_price", lambda *_args, **_kwargs: None)

    with pytest.raises(models.PurchaseOrderCreatedPending) as exc_info:
        checkout.lock_and_confirm_payment(
            client,
            _item([
                {"id": "sell-1", "price": "10.0"},
                {"id": "sell-2", "price": "10.0"},
            ]),
            _config("wechat"),
            **kwargs,
        )

    assert exc_info.value.batch_id == "batch-partial"
    assert exc_info.value.committed_amount == 10.0
    assert [record["buff_order_id"] for record in purchases] == ["bill-1"]
    assert client.shipping_calls == 0


def test_batch_finalize_unknown_persists_prior_successes_before_halting(monkeypatch):
    class BuffClient:
        _pay_method = "wechat"

        def try_batch_buy(self, *_args):
            return {
                "success": True,
                "batch_id": "batch-unknown",
                "pay_url": "https://pay.invalid/batch-unknown",
                "total_price": 20.0,
            }

        def batch_buy_find_and_finalize(self, *_args):
            exc = BuffWriteResultUnknown(
                "second finalize timed out",
                method="POST",
                url="https://buff.invalid/buy",
            )
            exc.partial_results = [
                {"id": "sell-1", "price": 10.0, "bill_order_id": "bill-1"}
            ]
            raise exc

    kwargs, _pending, purchases = _checkout_args(wait_result=True)
    monkeypatch.setattr(purchase_settlement, "_fetch_smart_market_price", lambda *_args, **_kwargs: None)

    with pytest.raises(models.PurchaseWriteResultUnknown) as exc_info:
        checkout.lock_and_confirm_payment(
            BuffClient(),
            _item(
                [
                    {"id": "sell-1", "price": "10.0"},
                    {"id": "sell-2", "price": "10.0"},
                ]
            ),
            _config("wechat"),
            **kwargs,
        )

    assert exc_info.value.batch_id == "batch-unknown"
    assert exc_info.value.committed_amount == 10.0
    assert [record["buff_order_id"] for record in purchases] == ["bill-1"]
    from app.services.buff_checkout_guard import get_unresolved_checkout

    unresolved = get_unresolved_checkout()
    assert unresolved["stage"] == "batch_finalize_unknown"
    assert unresolved["completed_order_ids"] == ["bill-1"]


def test_batch_created_without_pay_url_is_terminal_and_never_falls_back():
    class BuffClient:
        _pay_method = "wechat"

        def __init__(self):
            self.single_calls = 0

        def try_batch_buy(self, *_args):
            return {
                "success": False,
                "code": "CREATED_WITHOUT_PAY_URL",
                "created": True,
                "batch_id": "batch-created",
            }

        def lock_and_get_pay_url(self, *_args):
            self.single_calls += 1
            return {"success": False, "code": "FAIL"}

    client = BuffClient()
    kwargs, _pending, _purchases = _checkout_args()

    with pytest.raises(models.PurchaseOrderCreatedPending) as exc_info:
        checkout.lock_and_confirm_payment(
            client,
            _item([
                {"id": "sell-1", "price": "10.0"},
                {"id": "sell-2", "price": "10.0"},
            ]),
            _config("wechat"),
            **kwargs,
        )

    assert exc_info.value.batch_id == "batch-created"
    assert client.single_calls == 0


def test_single_created_without_pay_url_keeps_order_id_and_never_auto_resumes():
    class BuffClient:
        _pay_method = "alipay"

        def lock_and_get_pay_url(self, *_args):
            return {
                "success": False,
                "code": "CREATED_WITHOUT_PAY_URL",
                "created": True,
                "order_id": "bill-created",
                "msg": "verification required while reading payment link",
            }

    kwargs, _pending, purchases = _checkout_args()

    with pytest.raises(models.PurchaseOrderCreatedPending) as exc_info:
        checkout.lock_and_confirm_payment(
            BuffClient(),
            _item([{"id": "sell-1", "price": "10.0"}]),
            _config(),
            **kwargs,
        )

    assert exc_info.value.order_id == "bill-created"
    assert purchases == []


def test_cooling_down_has_dedicated_terminal_state():
    class BuffClient:
        _pay_method = "alipay"

        def lock_and_get_pay_url(self, *_args):
            return {"success": False, "code": "COOLING_DOWN"}

    kwargs, _pending, _purchases = _checkout_args()

    with pytest.raises(models.PurchaseCoolingDown):
        checkout.lock_and_confirm_payment(
            BuffClient(),
            _item([{"id": "sell-1", "price": "10.0"}]),
            _config(),
            **kwargs,
        )


class _FakeClock:
    """可控时钟：让真实 TTL 判定看到时间流逝，而不是打桩判定结果。"""

    def __init__(self):
        self.now = time.time()

    def time(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


def test_expired_snapshot_immediately_before_write_sends_no_post(monkeypatch):
    """本地校验耗时超过 TTL 后，写入前的快照校验必须拦住锁单请求。

    用真实 TTL 判定加可控时钟（在写请求前的预算核对处推进时间）驱动，不再
    打桩新鲜度函数，也不依赖它恰好被调用几次。
    """

    class BuffClient:
        _pay_method = "alipay"

        def __init__(self):
            self.get_calls = 0
            self.lock_calls = 0

        def get_sell_orders(self, _goods_id, _game):
            self.get_calls += 1
            return [{"id": "sell-1", "price": "10.0"}]

        def lock_and_get_pay_url(self, *_args):
            self.lock_calls += 1
            raise AssertionError("expired snapshot must not reach the write endpoint")

    clock = _FakeClock()
    monkeypatch.setattr(purchase_market, "time", clock)
    affordable_quantity = purchase_checkout._affordable_quantity

    def affordable_quantity_after_elapsed_checks(*args, **kwargs):
        clock.advance(10.0)
        return affordable_quantity(*args, **kwargs)

    monkeypatch.setattr(
        purchase_checkout,
        "_affordable_quantity",
        affordable_quantity_after_elapsed_checks,
    )
    client = BuffClient()
    kwargs, _pending, _purchases = _checkout_args()

    result = checkout.lock_and_confirm_payment(
        client,
        _item([{"id": "sell-1", "price": "10.0"}], fetched_at=clock.now),
        _config(),
        **kwargs,
    )

    assert result is None
    assert client.get_calls == 1
    assert client.lock_calls == 0


def test_invalid_zero_lowest_price_is_rejected_before_quantity_math():
    class BuffClient:
        _pay_method = "alipay"

        def lock_and_get_pay_url(self, *_args):
            raise AssertionError("invalid prices must not reach the write endpoint")

    kwargs, _pending, _purchases = _checkout_args()

    result = checkout.lock_and_confirm_payment(
        BuffClient(),
        _item([{"id": "sell-1", "price": "0"}]),
        _config(),
        **kwargs,
    )

    assert result is None


def test_unrecognised_non_ok_write_result_stays_unresolved():
    class BuffClient:
        _pay_method = "alipay"

        def lock_and_get_pay_url(self, *_args):
            return {"success": False, "code": "NEW_SERVER_STATE", "msg": "unknown"}

    kwargs, _pending, _purchases = _checkout_args()

    with pytest.raises(models.PurchaseWriteResultUnknown):
        checkout.lock_and_confirm_payment(
            BuffClient(),
            _item([{"id": "sell-1", "price": "10.0"}]),
            _config(),
            **kwargs,
        )

    from app.services.buff_checkout_guard import get_unresolved_checkout

    assert get_unresolved_checkout()["stage"] == "write_result_unknown"


def test_single_order_id_is_durable_before_payment_link_fetch_can_crash():
    class BuffClient:
        _pay_method = "alipay"

        def lock_and_get_pay_url(
            self,
            *_args,
            on_created=None,
        ):
            on_created("bill-before-crash")
            raise SystemExit("simulated hard stop during pay-link GET")

    kwargs, _pending, _purchases = _checkout_args()

    with pytest.raises(SystemExit):
        checkout.lock_and_confirm_payment(
            BuffClient(),
            _item([{"id": "sell-1", "price": "10.0"}]),
            _config(),
            **kwargs,
        )

    from app.services.buff_checkout_guard import get_unresolved_checkout

    unresolved = get_unresolved_checkout()
    assert unresolved["stage"] == "order_created"
    assert unresolved["order_id"] == "bill-before-crash"


def test_batch_id_is_durable_before_qr_fetch_can_crash():
    class BuffClient:
        _pay_method = "wechat"

        def try_batch_buy(self, *_args, on_created=None):
            on_created("batch-before-crash")
            raise SystemExit("simulated hard stop during QR GET")

    kwargs, _pending, _purchases = _checkout_args()

    with pytest.raises(SystemExit):
        checkout.lock_and_confirm_payment(
            BuffClient(),
            _item(
                [
                    {"id": "sell-1", "price": "10.0"},
                    {"id": "sell-2", "price": "10.0"},
                ]
            ),
            _config("wechat"),
            **kwargs,
        )

    from app.services.buff_checkout_guard import get_unresolved_checkout

    unresolved = get_unresolved_checkout()
    assert unresolved["stage"] == "batch_created"
    assert unresolved["batch_id"] == "batch-before-crash"


def test_each_batch_bill_id_is_durable_before_the_next_finalize_post():
    class BuffClient:
        _pay_method = "wechat"

        def try_batch_buy(self, *_args):
            return {
                "success": True,
                "batch_id": "batch-finalize-crash",
                "pay_url": "https://pay.invalid/batch-finalize-crash",
                "total_price": 20.0,
            }

        def batch_buy_find_and_finalize(self, *_args, on_match=None):
            first = {
                "id": "sell-1",
                "price": 10.0,
                "bill_order_id": "bill-before-next-post",
            }
            on_match(first, [first])
            raise SystemExit("simulated hard stop before next finalize")

    kwargs, _pending, _purchases = _checkout_args(wait_result=True)

    with pytest.raises(SystemExit):
        checkout.lock_and_confirm_payment(
            BuffClient(),
            _item(
                [
                    {"id": "sell-1", "price": "10.0"},
                    {"id": "sell-2", "price": "10.0"},
                ]
            ),
            _config("wechat"),
            **kwargs,
        )

    from app.services.buff_checkout_guard import get_unresolved_checkout

    unresolved = get_unresolved_checkout()
    assert unresolved["completed_order_ids"] == ["bill-before-next-post"]
    assert unresolved["partial_results"][0]["bill_order_id"] == (
        "bill-before-next-post"
    )


def test_all_batch_ids_are_durable_before_local_db_append_failure(monkeypatch):
    class BuffClient:
        _pay_method = "wechat"

        def try_batch_buy(self, *_args):
            return {
                "success": True,
                "batch_id": "batch-db-failure",
                "pay_url": "https://pay.invalid/batch-db-failure",
                "total_price": 20.0,
            }

        def batch_buy_find_and_finalize(self, *_args):
            return [
                {"id": "sell-1", "price": 10.0, "bill_order_id": "bill-1"},
                {"id": "sell-2", "price": 10.0, "bill_order_id": "bill-2"},
            ]

    kwargs, _pending, purchases = _checkout_args(wait_result=True)
    original_append = kwargs["append_purchase"]

    def fail_second_append(record):
        if purchases:
            raise OSError("simulated SQLite failure")
        original_append(record)

    kwargs["append_purchase"] = fail_second_append
    monkeypatch.setattr(
        purchase_settlement, "_fetch_smart_market_price",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(OSError, match="SQLite"):
        checkout.lock_and_confirm_payment(
            BuffClient(),
            _item(
                [
                    {"id": "sell-1", "price": "10.0"},
                    {"id": "sell-2", "price": "10.0"},
                ]
            ),
            _config("wechat"),
            **kwargs,
        )

    from app.services.buff_checkout_guard import get_unresolved_checkout

    unresolved = get_unresolved_checkout()
    assert unresolved["stage"] == "batch_matches_received"
    assert unresolved["completed_order_ids"] == ["bill-1", "bill-2"]


def test_automatic_shipping_prompt_is_disabled_by_default(monkeypatch):
    class BuffClient:
        _pay_method = "alipay"

        def __init__(self):
            self.shipping_calls = 0

        def lock_and_get_pay_url(self, *_args):
            return {
                "success": True,
                "order_id": "bill-no-auto-prompt",
                "pay_url": "https://pay.invalid/no-auto-prompt",
                "pay_type": "alipay",
            }

        def ask_seller_to_send(self, *_args):
            self.shipping_calls += 1
            raise AssertionError("automatic prompt should be disabled")

    client = BuffClient()
    kwargs, _pending, purchases = _checkout_args(wait_result=True)
    monkeypatch.setattr(
        purchase_settlement, "_fetch_smart_market_price",
        lambda *_args, **_kwargs: None,
    )

    paid = checkout.lock_and_confirm_payment(
        client,
        _item([{"id": "sell-1", "price": "10.0"}]),
        _config(),
        **kwargs,
    )

    assert paid == 10.0
    assert client.shipping_calls == 0
    assert len(purchases) == 1


def test_opted_in_shipping_prompt_unknown_halts_after_recording(monkeypatch):
    class BuffClient:
        _pay_method = "alipay"

        def lock_and_get_pay_url(self, *_args):
            return {
                "success": True,
                "order_id": "bill-shipping-unknown",
                "pay_url": "https://pay.invalid/shipping-unknown",
                "pay_type": "alipay",
            }

        def ask_seller_to_send(self, *_args):
            raise BuffWriteResultUnknown(
                "seller prompt timed out",
                method="POST",
                url="https://buff.invalid/ask",
            )

    config = _config()
    config["buff"]["auto_ask_seller_to_send"] = True
    kwargs, _pending, purchases = _checkout_args(wait_result=True)
    monkeypatch.setattr(
        purchase_settlement, "_fetch_smart_market_price",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(models.PurchaseWriteResultUnknown) as exc_info:
        checkout.lock_and_confirm_payment(
            BuffClient(),
            _item([{"id": "sell-1", "price": "10.0"}]),
            config,
            **kwargs,
        )

    from app.services.buff_checkout_guard import get_unresolved_checkout

    assert len(purchases) == 1
    assert exc_info.value.committed_amount == 10.0
    assert get_unresolved_checkout()["stage"] == "shipping_reminder_unknown"


def test_batch_shipping_prompt_unknown_preserves_all_committed_items(monkeypatch):
    class BuffClient:
        _pay_method = "wechat"

        def try_batch_buy(self, *_args):
            return {
                "success": True,
                "batch_id": "batch-shipping-unknown",
                "pay_url": "https://pay.invalid/batch-shipping-unknown",
                "total_price": 20.0,
            }

        def batch_buy_find_and_finalize(self, *_args):
            return [
                {"id": "sell-1", "price": 10.0, "bill_order_id": "bill-1"},
                {"id": "sell-2", "price": 10.0, "bill_order_id": "bill-2"},
            ]

        def ask_seller_to_send(self, *_args):
            raise BuffWriteResultUnknown(
                "batch seller prompt timed out",
                method="POST",
                url="https://buff.invalid/ask",
            )

    config = _config("wechat")
    config["buff"]["auto_ask_seller_to_send"] = True
    kwargs, _pending, purchases = _checkout_args(wait_result=True)
    monkeypatch.setattr(
        purchase_settlement, "_fetch_smart_market_price",
        lambda *_args, **_kwargs: None,
    )

    with pytest.raises(models.PurchaseWriteResultUnknown) as exc_info:
        checkout.lock_and_confirm_payment(
            BuffClient(),
            _item(
                [
                    {"id": "sell-1", "price": "10.0"},
                    {"id": "sell-2", "price": "10.0"},
                ]
            ),
            config,
            **kwargs,
        )

    from app.services.buff_checkout_guard import get_unresolved_checkout

    assert len(purchases) == 2
    assert exc_info.value.committed_amount == 20.0
    assert exc_info.value.committed_orders == 2
    unresolved = get_unresolved_checkout()
    assert unresolved["stage"] == "shipping_reminder_unknown"
    assert unresolved["completed_order_ids"] == ["bill-1", "bill-2"]


def test_guarded_runner_sets_error_for_unhandled_exception(monkeypatch):
    state = _FakeState()
    monkeypatch.setattr(pipeline_module, "get_state", lambda: state)
    monkeypatch.setattr(
        pipeline_module,
        "_run_pipeline",
        lambda _config: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    pipeline_module._run_pipeline_guarded({})

    assert state.status == ("error", "PIPELINE_UNEXPECTED_ERROR")
