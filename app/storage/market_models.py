"""Market models."""

from sqlmodel import Field, SQLModel


class ItemNameId(SQLModel, table=True):
    market_hash_name: str = Field(primary_key=True)
    item_nameid: str
