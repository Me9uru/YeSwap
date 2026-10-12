def test_steam_fee_estimates_keep_gross_and_proceeds_distinct():
    from utils.money import (
        estimated_purchase_discount_ratio,
        estimated_steam_gross_from_proceeds,
        estimated_steam_proceeds_from_gross,
    )

    assert round(estimated_steam_gross_from_proceeds(100.01), 2) == 115.01
    assert round(estimated_steam_proceeds_from_gross(115.0), 8) == 100.0
    assert round(estimated_purchase_discount_ratio(80.0, 115.0), 8) == 0.8


def test_manual_sale_proceeds_are_normalized_to_legacy_gross_price(monkeypatch):
    """The holdings dialog accepts net proceeds, while sale_price stays gross."""
    from app.services import transactions

    updates = []
    monkeypatch.setattr(
        transactions,
        "update_purchase_by_id",
        lambda db_id, data: updates.append((db_id, data)) or True,
    )

    from app.routes.transactions import TransactionUpdateBody
    body = TransactionUpdateBody(
        type="purchase",
        idx=99,
        db_id=42,
        sale_proceeds=100.01,
    )
    result = transactions.api_update_transaction(**body.model_dump())

    assert result["ok"] is True
    assert updates[0][0] == 42
    assert updates[0][1]["sale_price"] == 115.01
    assert updates[0][1]["sold_at"] > 0


def test_legacy_sale_price_clients_remain_gross_and_are_not_converted(monkeypatch):
    """Existing API clients and automatically synchronized data keep semantics."""
    from app.services import transactions

    updates = []
    monkeypatch.setattr(
        transactions,
        "update_purchase",
        lambda idx, data: updates.append((idx, data)) or True,
    )

    from app.routes.transactions import TransactionUpdateBody
    body = TransactionUpdateBody(
        type="purchase",
        idx=3,
        sale_price=115.01,
    )
    result = transactions.api_update_transaction(**body.model_dump())

    assert result["ok"] is True
    assert updates[0][0] == 3
    assert updates[0][1]["sale_price"] == 115.01


def test_explicit_sale_proceeds_take_precedence_for_mixed_version_clients(
    monkeypatch,
):
    from app.services import transactions

    updates = []
    monkeypatch.setattr(
        transactions,
        "update_purchase",
        lambda idx, data: updates.append(data) or True,
    )

    from app.routes.transactions import TransactionUpdateBody
    body = TransactionUpdateBody(
        type="purchase",
        idx=0,
        sale_proceeds=20,
        sale_price=20,
    )
    result = transactions.api_update_transaction(**body.model_dump())

    assert result["ok"] is True
    assert updates[0]["sale_price"] == 23.0
