import uuid
from collections.abc import Sequence

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app import service
from app.cache import ProductCache, get_cache
from app.db import get_session
from app.models import ReservationStatus, StockReservation
from app.schemas import ReservationItem, ReservationOut, ReservationRequest

router = APIRouter(prefix="/reservations", tags=["reservations"])


@router.post(
    "",
    response_model=ReservationOut,
    status_code=status.HTTP_201_CREATED,
    summary="Reserve stock for an order",
)
def create_reservation(
    payload: ReservationRequest,
    session: Session = Depends(get_session),
    cache: ProductCache = Depends(get_cache),
) -> ReservationOut:
    rows = service.reserve(session, payload.order_id, payload.items)
    session.commit()
    _invalidate(cache, rows)
    return _to_out(payload.order_id, rows, ReservationStatus.ACTIVE)


@router.post(
    "/{order_id}/commit", response_model=ReservationOut, summary="Mark a reservation committed"
)
def commit_reservation(
    order_id: uuid.UUID,
    session: Session = Depends(get_session),
) -> ReservationOut:
    rows = service.commit_reservation(session, order_id)
    session.commit()
    return _to_out(order_id, rows, ReservationStatus.COMMITTED)


@router.post("/{order_id}/release", response_model=ReservationOut, summary="Return reserved stock")
def release_reservation(
    order_id: uuid.UUID,
    session: Session = Depends(get_session),
    cache: ProductCache = Depends(get_cache),
) -> ReservationOut:
    rows = service.release_reservation(session, order_id)
    session.commit()
    _invalidate(cache, rows)
    return _to_out(order_id, rows, ReservationStatus.RELEASED)


def _invalidate(cache: ProductCache, rows: Sequence[StockReservation]) -> None:
    for sku in {row.product.sku for row in rows}:
        cache.invalidate(sku)


def _to_out(
    order_id: uuid.UUID, rows: Sequence[StockReservation], fallback: ReservationStatus
) -> ReservationOut:
    items = sorted(
        (ReservationItem(sku=row.product.sku, qty=row.qty) for row in rows),
        key=lambda item: item.sku,
    )
    return ReservationOut(
        order_id=order_id,
        status=rows[0].status if rows else fallback,
        items=items,
    )
