"""Transaction models."""

from typing import Optional
from sqlmodel import Field, SQLModel


class Purchase(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = ""
    goods_id: int = 0
    price: float = 0.0
    at: float = 0.0
    market_price: Optional[float] = None
    sale_price: Optional[float] = None
    sold_at: Optional[float] = None
    pending_receipt: Optional[bool] = None
    assetid: Optional[str] = None
    listing: Optional[bool] = None
    listing_status: Optional[str] = None
    # External BUFF identifiers make a completed checkout reconcilable after a
    # restart and allow the database to reject accidental duplicate recording.
    buff_order_id: Optional[str] = Field(default=None, index=True)
    buff_sell_order_id: Optional[str] = None
    batch_id: Optional[str] = Field(default=None, index=True)
    bill_order_id: Optional[str] = None


class Sale(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = ""
    goods_id: int = 0
    price: float = 0.0
    at: float = 0.0
    assetid: Optional[str] = None
