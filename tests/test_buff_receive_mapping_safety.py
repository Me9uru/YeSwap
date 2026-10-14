from curl_cffi.requests.exceptions import Timeout as CurlTimeout

from app.workflows import receiving as receive_flow


ITEM_NAME = "Danger Zone Case"


def test_accept_timeout_is_unknown_and_non_idempotent_post_is_not_retried(
    monkeypatch,
):
    calls = []

    class TimeoutSession:
        def post(self, *_args, **_kwargs):
            calls.append(True)
            raise CurlTimeout("response lost")

    monkeypatch.setattr(
        receive_flow,
        "build_steam_session",
        lambda **_kwargs: TimeoutSession(),
    )

    result = receive_flow.accept_steam_trade_offer(
        "offer-1",
        {"sessionid": "session"},
    )

    assert result is None
    assert calls == [True]


def test_exact_goods_id_match_beats_older_same_name_fallback():
    pending = [
        {
            "_db_id": 1,
            "goods_id": 999,
            "name": ITEM_NAME,
            "at": 1.0,
        },
        {
            "_db_id": 2,
            "goods_id": 42,
            "name": ITEM_NAME,
            "at": 2.0,
        },
    ]
    incoming = {
        "goods_id": 42,
        "market_hash_name": ITEM_NAME,
    }

    matched = receive_flow._match_purchase_for_item(incoming, pending, set())

    assert matched is not None
    assert matched["_db_id"] == 2


def test_ambiguous_name_only_match_is_rejected():
    pending = [
        {"_db_id": 1, "name": ITEM_NAME, "at": 1.0},
        {"_db_id": 2, "name": ITEM_NAME, "at": 2.0},
    ]
    incoming = {"market_hash_name": ITEM_NAME}

    assert (
        receive_flow._match_purchase_for_item(incoming, pending, set()) is None
    )


def test_partial_inventory_visibility_never_persists_seller_assetid(
    monkeypatch,
):
    purchases = [
        {
            "_db_id": 1,
            "goods_id": 42,
            "name": ITEM_NAME,
            "at": 1.0,
            "pending_receipt": True,
        },
        {
            "_db_id": 2,
            "goods_id": 42,
            "name": ITEM_NAME,
            "at": 2.0,
            "pending_receipt": True,
        },
    ]
    task = {
        "tradeofferid": "offer-1",
        "created_at": 100,
        "items": [
            {
                "assetid": "seller-old-asset-1",
                "goods_id": 42,
                "market_hash_name": ITEM_NAME,
            },
            {
                "assetid": "seller-old-asset-2",
                "goods_id": 42,
                "market_hash_name": ITEM_NAME,
            },
        ],
    }
    accepted = {"value": False}

    def get_purchases():
        return [dict(row) for row in purchases]

    def update_purchase_by_id(db_id, data):
        row = next(row for row in purchases if row["_db_id"] == db_id)
        row.update(data)
        return True

    def accept_offer(*_args, **_kwargs):
        accepted["value"] = True
        return True

    def scan_inventory():
        if not accepted["value"]:
            return True, [], ""
        # Steam inventory propagation is incomplete: only one of the two
        # received assets is visible so far.
        return (
            True,
            [
                {
                    "assetid": "receiver-new-asset-1",
                    "market_hash_name": ITEM_NAME,
                }
            ],
            "",
        )

    monkeypatch.setattr(
        receive_flow,
        "fetch_buff_steam_trade",
        lambda _client: (True, [task], ""),
    )
    monkeypatch.setattr(receive_flow, "accept_steam_trade_offer", accept_offer)
    monkeypatch.setattr(
        receive_flow,
        "jittered_sleep",
        lambda *_args, **_kwargs: None,
    )

    receive_flow.try_receive_once(
        get_purchases=get_purchases,
        update_purchase=lambda *_args, **_kwargs: False,
        get_buff_client=lambda: object(),
        get_steam_credentials=lambda: {
            "cookies": "steamLoginSecure=secure; sessionid=session",
        },
        scan_inventory=scan_inventory,
        update_purchase_by_id=update_purchase_by_id,
    )

    assigned = [row for row in purchases if row.get("assetid")]
    still_pending = [
        row
        for row in purchases
        if row.get("pending_receipt") and not row.get("assetid")
    ]

    assert [row["assetid"] for row in assigned] == ["receiver-new-asset-1"]
    assert [row["_db_id"] for row in still_pending] == [2]
    assert not {
        "seller-old-asset-1",
        "seller-old-asset-2",
    }.intersection(row.get("assetid") for row in purchases)


def test_inventory_poll_waits_for_all_new_assets_and_excludes_old_same_name(
    monkeypatch,
):
    purchases = [
        {
            "_db_id": 1,
            "goods_id": 42,
            "name": ITEM_NAME,
            "at": 1.0,
            "pending_receipt": True,
        },
        {
            "_db_id": 2,
            "goods_id": 42,
            "name": ITEM_NAME,
            "at": 2.0,
            "pending_receipt": True,
        },
    ]
    task = {
        "tradeofferid": "offer-1",
        "created_at": 100,
        "items": [
            {
                "assetid": "seller-old-asset-1",
                "goods_id": 42,
                "market_hash_name": ITEM_NAME,
            },
            {
                "assetid": "seller-old-asset-2",
                "goods_id": 42,
                "market_hash_name": ITEM_NAME,
            },
        ],
    }
    accepted = {"value": False}
    post_scans = {"value": 0}

    def get_purchases():
        return [dict(row) for row in purchases]

    def update_purchase_by_id(db_id, data):
        row = next(row for row in purchases if row["_db_id"] == db_id)
        row.update(data)
        return True

    def accept_offer(*_args, **_kwargs):
        accepted["value"] = True
        return True

    def inventory_item(assetid):
        return {"assetid": assetid, "market_hash_name": ITEM_NAME}

    def scan_inventory():
        existing = inventory_item("personal-existing-same-name")
        if not accepted["value"]:
            return True, [existing], ""
        post_scans["value"] += 1
        visible = [existing, inventory_item("receiver-new-asset-1")]
        if post_scans["value"] >= 2:
            visible.append(inventory_item("receiver-new-asset-2"))
        return True, visible, ""

    monkeypatch.setattr(
        receive_flow,
        "fetch_buff_steam_trade",
        lambda _client: (True, [task], ""),
    )
    monkeypatch.setattr(receive_flow, "accept_steam_trade_offer", accept_offer)
    monkeypatch.setattr(
        receive_flow,
        "jittered_sleep",
        lambda *_args, **_kwargs: None,
    )

    received = receive_flow.try_receive_once(
        get_purchases=get_purchases,
        update_purchase=lambda *_args, **_kwargs: False,
        get_buff_client=lambda: object(),
        get_steam_credentials=lambda: {
            "cookies": "steamLoginSecure=secure; sessionid=session",
        },
        scan_inventory=scan_inventory,
        update_purchase_by_id=update_purchase_by_id,
    )

    assert received == 2
    # 多扫一两次不应影响结论：只要求等到第二个资产可见，不锁死扫描次数。
    assert post_scans["value"] >= 2
    assert [row["assetid"] for row in purchases] == [
        "receiver-new-asset-1",
        "receiver-new-asset-2",
    ]


def test_failed_pre_accept_inventory_snapshot_leaves_offer_unaccepted(
    monkeypatch,
):
    purchases = [
        {
            "_db_id": 1,
            "goods_id": 42,
            "name": ITEM_NAME,
            "at": 1.0,
            "pending_receipt": True,
        }
    ]
    task = {
        "tradeofferid": "offer-1",
        "created_at": 100,
        "items": [
            {
                "assetid": "seller-old-asset",
                "goods_id": 42,
                "market_hash_name": ITEM_NAME,
            }
        ],
    }
    accepted = []

    monkeypatch.setattr(
        receive_flow,
        "fetch_buff_steam_trade",
        lambda _client: (True, [task], ""),
    )
    monkeypatch.setattr(
        receive_flow,
        "accept_steam_trade_offer",
        lambda *_args, **_kwargs: accepted.append(True) or True,
    )

    received = receive_flow.try_receive_once(
        get_purchases=lambda: [dict(row) for row in purchases],
        update_purchase=lambda *_args, **_kwargs: False,
        get_buff_client=lambda: object(),
        get_steam_credentials=lambda: {
            "cookies": "steamLoginSecure=secure; sessionid=session",
        },
        scan_inventory=lambda: (False, [], "inventory unavailable"),
        update_purchase_by_id=lambda *_args, **_kwargs: True,
    )

    assert received == 0
    assert accepted == []
    assert purchases[0]["pending_receipt"] is True
    assert not purchases[0].get("assetid")


def test_unmatched_offer_is_never_accepted_or_scanned(monkeypatch):
    purchases = [
        {
            "_db_id": 1,
            "goods_id": 999,
            "name": ITEM_NAME,
            "at": 1.0,
            "pending_receipt": True,
        }
    ]
    task = {
        "tradeofferid": "offer-foreign",
        "created_at": 100,
        "items": [
            {
                "assetid": "seller-old-asset",
                "goods_id": 42,
                "market_hash_name": ITEM_NAME,
            }
        ],
    }
    accepted = []
    scans = []

    monkeypatch.setattr(
        receive_flow,
        "fetch_buff_steam_trade",
        lambda _client: (True, [task], ""),
    )
    monkeypatch.setattr(
        receive_flow,
        "accept_steam_trade_offer",
        lambda *_args, **_kwargs: accepted.append(True) or True,
    )

    received = receive_flow.try_receive_once(
        get_purchases=lambda: [dict(row) for row in purchases],
        update_purchase=lambda *_args, **_kwargs: False,
        get_buff_client=lambda: object(),
        get_steam_credentials=lambda: {
            "cookies": "steamLoginSecure=secure; sessionid=session",
        },
        scan_inventory=lambda: scans.append(True) or (True, [], ""),
        update_purchase_by_id=lambda *_args, **_kwargs: True,
    )

    assert received == 0
    assert accepted == []
    assert scans == []


def test_unknown_accept_result_is_reconciled_from_inventory(monkeypatch):
    purchases = [
        {
            "_db_id": 1,
            "goods_id": 42,
            "name": ITEM_NAME,
            "at": 1.0,
            "pending_receipt": True,
        }
    ]
    task = {
        "tradeofferid": "offer-unknown",
        "created_at": 100,
        "items": [
            {
                "assetid": "seller-old-asset",
                "goods_id": 42,
                "market_hash_name": ITEM_NAME,
            }
        ],
    }
    accepted = {"attempted": False}

    def get_purchases():
        return [dict(row) for row in purchases]

    def update_purchase_by_id(db_id, data):
        row = next(row for row in purchases if row["_db_id"] == db_id)
        row.update(data)
        return True

    def accept_unknown(*_args, **_kwargs):
        accepted["attempted"] = True
        return None

    def scan_inventory():
        if not accepted["attempted"]:
            return True, [], ""
        return (
            True,
            [
                {
                    "assetid": "receiver-new-asset",
                    "market_hash_name": ITEM_NAME,
                }
            ],
            "",
        )

    monkeypatch.setattr(
        receive_flow,
        "fetch_buff_steam_trade",
        lambda _client: (True, [task], ""),
    )
    monkeypatch.setattr(
        receive_flow,
        "accept_steam_trade_offer",
        accept_unknown,
    )
    monkeypatch.setattr(
        receive_flow,
        "jittered_sleep",
        lambda *_args, **_kwargs: None,
    )

    received = receive_flow.try_receive_once(
        get_purchases=get_purchases,
        update_purchase=lambda *_args, **_kwargs: False,
        get_buff_client=lambda: object(),
        get_steam_credentials=lambda: {
            "cookies": "steamLoginSecure=secure; sessionid=session",
        },
        scan_inventory=scan_inventory,
        update_purchase_by_id=update_purchase_by_id,
    )

    assert received == 1
    assert purchases[0]["assetid"] == "receiver-new-asset"
    assert purchases[0]["pending_receipt"] is False


def test_delivered_order_reconciles_pending_receipt_from_inventory():
    purchases = [
        {
            "_db_id": 1,
            "name": ITEM_NAME,
            "at": 1.0,
            "pending_receipt": True,
            "buff_order_id": "order-ok",
        },
        {
            "_db_id": 2,
            "name": ITEM_NAME,
            "at": 2.0,
            "pending_receipt": True,
            "buff_order_id": "order-delivering",
        },
        {
            "_db_id": 3,
            "name": "Manual Item",
            "at": 3.0,
            "pending_receipt": True,
        },
        {
            "_db_id": 4,
            "name": "Ghost Item",
            "at": 4.0,
            "pending_receipt": True,
            "buff_order_id": "order-ok-no-stock",
        },
    ]
    queried = []
    states = {
        "order-ok": {"status": "paid", "state": "SUCCESS"},
        "order-delivering": {"status": "paid", "state": "DELIVERING"},
        # 订单已交付但库存尚未出现该物品（Steam 最终一致窗口）
        "order-ok-no-stock": {"status": "paid", "state": "SUCCESS"},
    }

    class Client:
        def get_bill_order_pay_state(self, order_id, game="csgo"):
            queried.append(order_id)
            return states[order_id]

    inventory = [
        {"assetid": "asset-b", "market_hash_name": ITEM_NAME},
        {"assetid": "asset-a", "market_hash_name": ITEM_NAME},
        {"assetid": "asset-x", "market_hash_name": "Manual Item"},
    ]

    def update_purchase_by_id(db_id, data):
        row = next(r for r in purchases if r["_db_id"] == db_id)
        row.update(data)
        return True

    reconciled = receive_flow.reconcile_delivered_purchases(
        get_purchases=lambda: [dict(r) for r in purchases],
        get_buff_client=lambda: Client(),
        scan_inventory=lambda: (True, list(inventory), ""),
        update_purchase_by_id=update_purchase_by_id,
    )

    assert reconciled == 1
    # 只查询「待收货 + 有订单号」的记录；补录记录不产生 BUFF 查询
    assert queried == ["order-ok", "order-delivering", "order-ok-no-stock"]
    # 同名的多个库存物品按 assetid 确定性分配，只绑定一个
    assert purchases[0]["assetid"] == "asset-a"
    assert purchases[0]["pending_receipt"] is False
    # 未交付（DELIVERING）与库存未出现的记录保持待收货
    assert purchases[1].get("assetid") is None
    assert purchases[1]["pending_receipt"] is True
    assert purchases[3].get("assetid") is None
    assert purchases[3]["pending_receipt"] is True
    # 无订单号的补录记录不受影响
    assert purchases[2].get("assetid") is None
    assert purchases[2]["pending_receipt"] is True


def test_reconcile_keeps_pending_when_inventory_scan_fails():
    purchases = [
        {
            "_db_id": 1,
            "name": ITEM_NAME,
            "at": 1.0,
            "pending_receipt": True,
            "buff_order_id": "order-ok",
        },
    ]

    class Client:
        def get_bill_order_pay_state(self, order_id, game="csgo"):
            return {"status": "paid", "state": "SUCCESS"}

    def update_purchase_by_id(db_id, data):
        purchases[0].update(data)
        return True

    reconciled = receive_flow.reconcile_delivered_purchases(
        get_purchases=lambda: [dict(r) for r in purchases],
        get_buff_client=lambda: Client(),
        scan_inventory=lambda: (False, [], "inventory unavailable"),
        update_purchase_by_id=update_purchase_by_id,
    )

    assert reconciled == 0
    assert purchases[0]["pending_receipt"] is True
    assert not purchases[0].get("assetid")
