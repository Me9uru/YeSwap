import pytest


def test_buff_trade_poll_uses_shared_client_and_parses_pending_items(monkeypatch):
    from app.workflows.receiving import fetch_buff_steam_trade

    class Client:
        def get_steam_trades(self):
            return [
                {
                    "state": 1,
                    "tradeofferid": "offer-1",
                    "created_at": 123,
                    "items_to_trade": [{"assetid": "asset-1", "goods_id": "42"}],
                    "goods_infos": {
                        "42": {
                            "name": "测试物品",
                            "market_hash_name": "Test Item",
                        }
                    },
                }
            ]

    client = Client()
    ok, pending, error = fetch_buff_steam_trade(client)

    assert ok is True
    assert error == ""
    assert pending == [
        {
            "tradeofferid": "offer-1",
            "created_at": 123,
            "items": [
                {
                    "assetid": "asset-1",
                    "name": "测试物品",
                    "market_hash_name": "Test Item",
                    "goods_id": 42,
                }
            ],
        }
    ]


def test_buff_trade_poll_propagates_request_circuit():
    from app.workflows.receiving import fetch_buff_steam_trade
    from buff import BuffRateLimited

    class Client:
        def get_steam_trades(self):
            raise BuffRateLimited(30)

    with pytest.raises(BuffRateLimited):
        fetch_buff_steam_trade(Client())


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        # 认证标志位
        ({"status": "error", "step": "BUFF_AUTH_EXPIRED", "buff_auth_expired": True}, False),
        ({"status": "error", "step": "BUFF_VERIFICATION_REQUIRED", "buff_verification_required": True}, False),
        # 待对账步骤（可能已创建订单）
        ({"status": "error", "step": "BUFF_ORDER_CREATED_PENDING"}, False),
        # 控制行：完全空闲时才允许后台请求
        ({"status": "idle", "step": ""}, True),
    ],
)
def test_buff_background_poll_only_runs_while_fully_idle(monkeypatch, status, expected):
    from app.background import safety as workers

    monkeypatch.setattr(workers, "get_status", lambda: status)
    assert workers._buff_background_request_is_safe() is expected
