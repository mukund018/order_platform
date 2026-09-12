"""Payment rules.

The whole job of this service is to make sure one order is charged at most once, no
matter how many times orders-service asks. Everything interesting here is about that.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.gateway import Gateway, GatewayResult
from app.metrics import PAYMENT_ATTEMPTS, PAYMENT_GATEWAY_DURATION
from app.models import Payment, PaymentStatus
from common.errors import ConflictError, NotFoundError
from common.logging import get_logger

log = get_logger(__name__)


def find_payment(session: Session, order_id: uuid.UUID) -> Payment | None:
    return session.execute(select(Payment).where(Payment.order_id == order_id)).scalar_one_or_none()


def get_payment(session: Session, order_id: uuid.UUID) -> Payment:
    payment = find_payment(session, order_id)
    if payment is None:
        raise NotFoundError(f"no payment for order {order_id}", details={"order_id": str(order_id)})
    return payment


def charge(session: Session, order_id: uuid.UUID, amount_paise: int, gateway: Gateway) -> Payment:
    """Charge an order, or return the payment that already settled it.

    Raises ConflictError if another attempt is still running or the amount does not
    match what was originally requested for this order.
    """
    payment = find_payment(session, order_id)
    created = False
    if payment is None:
        payment, created = _open_attempt(session, order_id, amount_paise)

    if payment.amount_paise != amount_paise:
        # Two different totals for one order means the caller has a bug or two orders
        # share an id. Guessing which amount is right is how customers get overcharged.
        raise ConflictError(
            f"order {order_id} already has a payment for {payment.amount_paise} paise",
            code="PAYMENT_AMOUNT_MISMATCH",
            details={"order_id": str(order_id), "amount_paise": payment.amount_paise},
        )

    if not created:
        if payment.status is PaymentStatus.SUCCEEDED:
            log.info(
                "payment_replayed",
                order_id=str(order_id),
                payment_id=str(payment.id),
                provider_ref=payment.provider_ref,
            )
            return payment
        if payment.status is PaymentStatus.PENDING:
            # A row can also sit in PENDING because a previous attempt died between the
            # commit and the gateway answering. Releasing it automatically would risk a
            # second charge, so it needs a human to reconcile against the gateway.
            raise ConflictError(
                f"a payment attempt for order {order_id} is already in progress",
                code="PAYMENT_IN_PROGRESS",
                details={"order_id": str(order_id), "payment_id": str(payment.id)},
            )
        _reopen(session, payment)

    return _call_gateway(session, payment, gateway)


def _open_attempt(session: Session, order_id: uuid.UUID, amount_paise: int) -> tuple[Payment, bool]:
    """Insert the PENDING row, or lose the race and return the winner's row."""
    payment = Payment(order_id=order_id, amount_paise=amount_paise, status=PaymentStatus.PENDING)
    session.add(payment)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        winner = find_payment(session, order_id)
        if winner is None:
            raise
        log.info("payment_insert_raced", order_id=str(order_id), payment_id=str(winner.id))
        return winner, False
    return payment, True


def _reopen(session: Session, payment: Payment) -> None:
    """Move a FAILED payment back to PENDING so a retry holds the slot."""
    payment.status = PaymentStatus.PENDING
    payment.failure_reason = None
    session.commit()


def _call_gateway(session: Session, payment: Payment, gateway: Gateway) -> Payment:
    order_id = payment.order_id
    # Deliberately outside a transaction: the gateway call can take seconds, and a
    # connection held open that long starves the pool for everyone else.
    with PAYMENT_GATEWAY_DURATION.time():
        result: GatewayResult = gateway.charge(order_id, payment.amount_paise)

    payment.attempts += 1
    if result.approved:
        payment.status = PaymentStatus.SUCCEEDED
        payment.provider_ref = result.provider_ref
        payment.failure_reason = None
    else:
        payment.status = PaymentStatus.FAILED
        payment.failure_reason = result.failure_reason
    session.commit()

    PAYMENT_ATTEMPTS.labels("succeeded" if result.approved else "failed").inc()
    log.info(
        "payment_settled",
        order_id=str(order_id),
        payment_id=str(payment.id),
        status=payment.status.value,
        amount_paise=payment.amount_paise,
        attempts=payment.attempts,
        provider_ref=payment.provider_ref,
        failure_reason=payment.failure_reason,
        duration_ms=result.latency_ms,
    )
    return payment
