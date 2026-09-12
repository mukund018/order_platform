import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Query, status
from sqlalchemy.orm import Session

from app import service
from app.clients.inventory import InventoryClient, get_inventory_client
from app.clients.payments import PaymentsClient, get_payments_client
from app.db import get_session
from app.models import Order, OrderStatus
from app.schemas import OrderCreate, OrderDetail, OrderOut
from common.errors import AppError

router = APIRouter(prefix="/orders", tags=["orders"])

# The width of orders.idempotency_key. A longer key would only fail at the insert.
MAX_KEY_LENGTH = 64


@router.post(
    "",
    response_model=OrderDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Place an order",
)
def create_order(
    payload: OrderCreate,
    # Declared optional so that a missing header is our own 400 with a useful code,
    # rather than FastAPI's generic 422. The description is what /docs shows.
    idempotency_key: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            description="Required. Re-sending the same key returns the original order.",
        ),
    ] = None,
    session: Session = Depends(get_session),
    inventory: InventoryClient = Depends(get_inventory_client),
    payments: PaymentsClient = Depends(get_payments_client),
) -> Order:
    # An order that comes back FAILED is still a 201: the request was handled, and the
    # answer is in the body. Returning 4xx for a declined card would have every caller
    # retrying something that will never succeed.
    return service.create_order(
        session, payload, _require_key(idempotency_key), inventory, payments
    )


@router.get("", response_model=list[OrderOut], summary="List orders")
def list_orders(
    order_status: Annotated[OrderStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    session: Session = Depends(get_session),
) -> list[Order]:
    return service.list_orders(session, status=order_status, limit=limit, offset=offset)


@router.get("/{order_id}", response_model=OrderDetail, summary="One order with its history")
def get_order(order_id: uuid.UUID, session: Session = Depends(get_session)) -> Order:
    return service.get_order(session, order_id)


@router.post("/{order_id}/cancel", response_model=OrderDetail, summary="Cancel a confirmed order")
def cancel_order(
    order_id: uuid.UUID,
    session: Session = Depends(get_session),
    inventory: InventoryClient = Depends(get_inventory_client),
) -> Order:
    return service.cancel_order(session, order_id, inventory)


def _require_key(idempotency_key: str | None) -> str:
    """The header is mandatory. Without it a retried POST places a second order, and the
    caller has no way to tell that from a slow first one."""
    key = (idempotency_key or "").strip()
    if not key:
        raise AppError(
            "Idempotency-Key header is required",
            code="IDEMPOTENCY_KEY_REQUIRED",
            status_code=400,
        )
    if len(key) > MAX_KEY_LENGTH:
        raise AppError(
            f"Idempotency-Key must be at most {MAX_KEY_LENGTH} characters",
            code="IDEMPOTENCY_KEY_TOO_LONG",
            status_code=400,
        )
    return key
