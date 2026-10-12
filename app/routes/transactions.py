"""Transaction HTTP endpoints, delegating business operations to a service."""

from typing import Optional
from fastapi import APIRouter
from pydantic import BaseModel
from app.services import transactions as service

router = APIRouter()


class AddPurchaseBody(BaseModel):
    name: str = ""
    price: float = 0
    quantity: int = 1
    goods_id: Optional[int] = None
    steam_link: Optional[str] = None
    assetid: Optional[str] = None


class TransactionUpdateBody(BaseModel):
    type: str = "purchase"
    idx: int = 0
    db_id: Optional[int] = None
    name: Optional[str] = None
    price: Optional[float] = None
    goods_id: Optional[int] = None
    market_price: Optional[float] = None
    sale_proceeds: Optional[float] = None
    sale_price: Optional[float] = None
    pending_receipt: Optional[bool] = None
    assetid: Optional[str] = None
    listing: Optional[bool] = None


@router.post("/api/purchase")
def api_add_purchase(body: AddPurchaseBody):
    return service.api_add_purchase(**body.model_dump())


@router.get("/api/transactions")
def api_transactions(enrich_current_price: bool = False):
    return service.api_transactions(enrich_current_price=enrich_current_price)


@router.delete("/api/transaction")
def api_delete_transaction(type: str = "purchase", idx: int = 0, db_id: int = 0):
    return service.api_delete_transaction(type=type, idx=idx, db_id=db_id)


@router.put("/api/transaction")
def api_update_transaction(body: TransactionUpdateBody):
    return service.api_update_transaction(**body.model_dump())


@router.get("/api/stats")
def api_stats():
    return service.api_stats()


@router.post("/api/purchase/{idx}/delist")
def api_delist_purchase(idx: int, db_id: int = 0):
    return service.api_delist_purchase(idx=idx, db_id=db_id)


@router.post("/api/sync_sold_from_history")
def api_sync_sold_from_history():
    return service.api_sync_sold_from_history()


@router.post("/api/repair_error_records")
def api_repair_error_records():
    return service.api_repair_error_records()
