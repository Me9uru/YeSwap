from app.storage import transactions as transaction_store


def test_purchase_round_trip_keeps_buff_reconciliation_ids():
    transaction_store.db_append_purchase(
        {
            "name": "Test Item",
            "goods_id": 42,
            "price": 10.0,
            "at": 123.0,
            "pending_receipt": True,
            "buff_order_id": "bill-1",
            "buff_sell_order_id": "sell-1",
            "batch_id": "batch-1",
            "bill_order_id": "bill-1",
        }
    )

    [saved] = transaction_store.db_get_purchases()
    assert saved["buff_order_id"] == "bill-1"
    assert saved["buff_sell_order_id"] == "sell-1"
    assert saved["batch_id"] == "batch-1"
    assert saved["bill_order_id"] == "bill-1"
