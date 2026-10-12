"""Transactions."""

import json
import os
from sqlmodel import select
from app.storage.transaction_models import (
    Purchase,
    Sale,
)
from app.storage.connection import (
    _CONFIG_DIR,
    get_session,
)


_TRANSACTIONS_JSON = _CONFIG_DIR / "transactions.json"


_TRANSACTIONS_BAK = _CONFIG_DIR / "transactions.json.bak"


def _purchase_from_dict(d: dict) -> Purchase:
    return Purchase(
        name=d.get("name", ""),
        goods_id=int(d.get("goods_id", 0) or 0),
        price=float(d.get("price", 0)),
        at=float(d.get("at", 0)),
        market_price=float(d["market_price"]) if d.get("market_price") is not None else None,
        sale_price=float(d["sale_price"]) if d.get("sale_price") is not None else None,
        sold_at=float(d["sold_at"]) if d.get("sold_at") is not None else None,
        pending_receipt=bool(d["pending_receipt"])
        if d.get("pending_receipt") is not None
        else None,
        assetid=str(d["assetid"]) if d.get("assetid") is not None else None,
        listing=bool(d["listing"]) if d.get("listing") is not None else None,
        listing_status=str(d["listing_status"]) if d.get("listing_status") is not None else None,
        buff_order_id=str(d["buff_order_id"]) if d.get("buff_order_id") else None,
        buff_sell_order_id=str(d["buff_sell_order_id"]) if d.get("buff_sell_order_id") else None,
        batch_id=str(d["batch_id"]) if d.get("batch_id") else None,
        bill_order_id=str(d["bill_order_id"]) if d.get("bill_order_id") else None,
    )


def _sale_from_dict(d: dict) -> Sale:
    return Sale(
        name=d.get("name", ""),
        goods_id=int(d.get("goods_id", 0) or 0),
        price=float(d.get("price", 0)),
        at=float(d.get("at", 0)),
        assetid=str(d["assetid"]) if d.get("assetid") is not None else None,
    )


def _purchase_to_dict(p: Purchase) -> dict:
    d = {
        "_db_id": p.id,
        "name": p.name,
        "goods_id": p.goods_id,
        "price": p.price,
        "at": p.at,
    }
    if p.market_price is not None:
        d["market_price"] = p.market_price
    if p.sale_price is not None:
        d["sale_price"] = p.sale_price
    if p.sold_at is not None:
        d["sold_at"] = p.sold_at
    if p.pending_receipt is not None:
        d["pending_receipt"] = p.pending_receipt
    if p.assetid is not None:
        d["assetid"] = p.assetid
    if p.listing is not None:
        d["listing"] = p.listing
    if p.listing_status is not None:
        d["listing_status"] = p.listing_status
    if p.buff_order_id is not None:
        d["buff_order_id"] = p.buff_order_id
    if p.buff_sell_order_id is not None:
        d["buff_sell_order_id"] = p.buff_sell_order_id
    if p.batch_id is not None:
        d["batch_id"] = p.batch_id
    if p.bill_order_id is not None:
        d["bill_order_id"] = p.bill_order_id
    return d


def _sale_to_dict(s: Sale) -> dict:
    d = {
        "name": s.name,
        "goods_id": s.goods_id,
        "price": s.price,
        "at": s.at,
    }
    if s.assetid is not None:
        d["assetid"] = s.assetid
    return d


def migrate_from_json() -> bool:
    """
    One-time migration: read transactions.json → insert into SQLite →
    rename JSON to .bak.  Returns True if migration happened.
    """
    if not _TRANSACTIONS_JSON.exists():
        return False
    with get_session() as session:
        existing = session.exec(select(Purchase).limit(1)).first()
        if existing is not None:
            return False
    try:
        with open(_TRANSACTIONS_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return False
    purchases = data.get("purchases", [])
    sales = data.get("sales", [])
    with get_session() as session:
        for p in purchases:
            session.add(_purchase_from_dict(p))
        for s in sales:
            session.add(_sale_from_dict(s))
        session.commit()
    try:
        if _TRANSACTIONS_BAK.exists():
            os.remove(str(_TRANSACTIONS_BAK))
        os.rename(str(_TRANSACTIONS_JSON), str(_TRANSACTIONS_BAK))
    except OSError:
        pass
    return True


_PURCHASE_UPDATABLE = frozenset(
    {
        "name",
        "price",
        "goods_id",
        "market_price",
        "sale_price",
        "sold_at",
        "pending_receipt",
        "assetid",
        "listing",
        "listing_status",
        "buff_order_id",
        "buff_sell_order_id",
        "batch_id",
        "bill_order_id",
    }
)


_SALE_UPDATABLE = frozenset({"name", "price", "goods_id", "assetid", "at"})


def db_append_purchase(p: dict) -> None:
    with get_session() as session:
        session.add(_purchase_from_dict(p))
        session.commit()


def db_get_purchases() -> list:
    with get_session() as session:
        rows = session.exec(select(Purchase).order_by(Purchase.id)).all()
        return [_purchase_to_dict(r) for r in rows]


def db_append_sale(s: dict) -> None:
    with get_session() as session:
        session.add(_sale_from_dict(s))
        session.commit()


def db_get_sales() -> list:
    with get_session() as session:
        rows = session.exec(select(Sale).order_by(Sale.id)).all()
        return [_sale_to_dict(r) for r in rows]


def db_clear_transactions() -> None:
    from sqlmodel import delete as sql_delete

    with get_session() as session:
        session.exec(sql_delete(Purchase))
        session.exec(sql_delete(Sale))
        session.commit()


def db_replace_transactions(purchases: list, sales: list) -> None:
    from sqlmodel import delete as sql_delete

    with get_session() as session:
        session.exec(sql_delete(Purchase))
        session.exec(sql_delete(Sale))
        for p in purchases:
            session.add(_purchase_from_dict(p))
        for s in sales:
            session.add(_sale_from_dict(s))
        session.commit()


def db_delete_purchase(idx: int) -> bool:
    """Delete purchase by positional index (0-based, ordered by id)."""
    with get_session() as session:
        rows = session.exec(select(Purchase).order_by(Purchase.id)).all()
        if 0 <= idx < len(rows):
            session.delete(rows[idx])
            session.commit()
            return True
    return False


def db_delete_sale(idx: int) -> bool:
    with get_session() as session:
        rows = session.exec(select(Sale).order_by(Sale.id)).all()
        if 0 <= idx < len(rows):
            session.delete(rows[idx])
            session.commit()
            return True
    return False


def db_update_purchase(idx: int, data: dict) -> bool:
    """按位置索引更新（兼容旧接口，UI 路由使用）。"""
    with get_session() as session:
        rows = session.exec(select(Purchase).order_by(Purchase.id)).all()
        if 0 <= idx < len(rows):
            row = rows[idx]
            for k, v in data.items():
                if k in _PURCHASE_UPDATABLE:
                    setattr(row, k, v)
            session.add(row)
            session.commit()
            return True
    return False


def db_update_purchase_by_id(db_id: int, data: dict) -> bool:
    """按主键 ID 更新，O(1) 操作，推荐内部 worker 使用。"""
    if not db_id:
        return False
    with get_session() as session:
        row = session.get(Purchase, db_id)
        if row is None:
            return False
        for k, v in data.items():
            if k in _PURCHASE_UPDATABLE:
                setattr(row, k, v)
        session.add(row)
        session.commit()
        return True


def db_delete_purchase_by_id(db_id: int) -> bool:
    """按主键 ID 删除，O(1) 操作。"""
    if not db_id:
        return False
    with get_session() as session:
        row = session.get(Purchase, db_id)
        if row is None:
            return False
        session.delete(row)
        session.commit()
        return True


def db_update_sale(idx: int, data: dict) -> bool:
    with get_session() as session:
        rows = session.exec(select(Sale).order_by(Sale.id)).all()
        if 0 <= idx < len(rows):
            row = rows[idx]
            for k, v in data.items():
                if k in _SALE_UPDATABLE:
                    setattr(row, k, v)
            session.add(row)
            session.commit()
            return True
    return False
