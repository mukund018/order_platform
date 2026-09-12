import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models import OrderStatus

EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"

MAX_ITEMS = 20


class OrderItemIn(BaseModel):
    sku: str = Field(min_length=1, max_length=32)
    qty: int = Field(gt=0, le=100)


class OrderCreate(BaseModel):
    customer_email: str = Field(min_length=3, max_length=254, pattern=EMAIL_PATTERN)
    items: list[OrderItemIn] = Field(min_length=1, max_length=MAX_ITEMS)

    @model_validator(mode="after")
    def _reject_repeated_skus(self) -> "OrderCreate":
        # inventory-service rejects a reservation that names the same sku twice, so
        # catching it here keeps the failure at the edge instead of half way through
        # an order that has already been written.
        skus = [item.sku for item in self.items]
        duplicates = sorted({sku for sku in skus if skus.count(sku) > 1})
        if duplicates:
            raise ValueError(f"duplicate sku in request: {', '.join(duplicates)}")
        return self


class OrderItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    product_id: uuid.UUID
    sku: str
    qty: int
    unit_price_paise: int


class OrderEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    from_status: OrderStatus | None
    to_status: OrderStatus
    reason: str | None
    request_id: str | None
    created_at: datetime


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    customer_email: str
    status: OrderStatus
    total_paise: int
    failure_reason: str | None
    created_at: datetime
    updated_at: datetime


class OrderDetail(OrderOut):
    items: list[OrderItemOut]
    events: list[OrderEventOut]


class DailyReport(BaseModel):
    date: date
    timezone: str
    window_start: datetime
    window_end: datetime
    orders: int
    confirmed_orders: int
    revenue_paise: int
