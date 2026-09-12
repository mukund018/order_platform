import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models import ReservationStatus


class ProductCreate(BaseModel):
    sku: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=200)
    price_paise: int = Field(gt=0)
    stock: int = Field(0, ge=0)


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    sku: str
    name: str
    price_paise: int
    stock: int
    updated_at: datetime


class StockAdjustRequest(BaseModel):
    delta: int = Field(description="Units to add; negative removes them")


class ReservationItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sku: str = Field(min_length=1, max_length=32)
    qty: int = Field(gt=0)


class ReservationRequest(BaseModel):
    order_id: uuid.UUID
    items: list[ReservationItem] = Field(min_length=1)


class ReservationOut(BaseModel):
    order_id: uuid.UUID
    status: ReservationStatus
    items: list[ReservationItem]


class ActiveReservationOut(BaseModel):
    """One ACTIVE reservation row, for cross-service reconciliation (INC-009) - not
    grouped by order, because the question this answers is "is this row still owned
    by a live order", not "what does this order's cart look like"."""

    model_config = ConfigDict(from_attributes=True)

    order_id: uuid.UUID
    sku: str
    qty: int
    created_at: datetime
