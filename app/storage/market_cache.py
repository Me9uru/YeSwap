"""Market cache."""

from typing import Optional
from sqlmodel import select
from app.storage.market_models import (
    ItemNameId,
)
from app.storage.connection import (
    get_session,
)


def db_get_item_nameid(market_hash_name: str) -> Optional[str]:
    with get_session() as session:
        item = session.exec(
            select(ItemNameId).where(ItemNameId.market_hash_name == market_hash_name)
        ).first()
        return item.item_nameid if item else None


def db_set_item_nameid(market_hash_name: str, item_nameid: str) -> None:
    with get_session() as session:
        item = session.exec(
            select(ItemNameId).where(ItemNameId.market_hash_name == market_hash_name)
        ).first()
        if item:
            item.item_nameid = item_nameid
        else:
            item = ItemNameId(market_hash_name=market_hash_name, item_nameid=item_nameid)
        session.add(item)
        session.commit()
