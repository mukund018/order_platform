import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.gateway import Gateway, get_gateway
from app.models import Payment
from app.schemas import PaymentCreate, PaymentRead
from app.service import charge, get_payment

router = APIRouter(prefix="/payments", tags=["payments"])


@router.post("", response_model=PaymentRead, summary="Charge an order")
def create_payment(
    payload: PaymentCreate,
    session: Session = Depends(get_session),
    gateway: Gateway = Depends(get_gateway),
) -> Payment:
    # A decline is a 200 with status FAILED, not an HTTP error: the request was
    # handled correctly and the answer is "the bank said no". Callers that treat 4xx
    # as "retry me" would otherwise hammer the gateway with a card that will never work.
    return charge(session, payload.order_id, payload.amount_paise, gateway)


@router.get("/{order_id}", response_model=PaymentRead, summary="Payment for an order")
def read_payment(order_id: uuid.UUID, session: Session = Depends(get_session)) -> Payment:
    return get_payment(session, order_id)
