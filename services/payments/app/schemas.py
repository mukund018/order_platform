import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models import PaymentStatus


class PaymentCreate(BaseModel):
    order_id: uuid.UUID
    amount_paise: int = Field(gt=0)


class PaymentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    order_id: uuid.UUID
    amount_paise: int
    status: PaymentStatus
    provider_ref: str | None
    failure_reason: str | None
    attempts: int
    created_at: datetime
