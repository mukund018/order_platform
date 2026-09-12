"""The order state machine.

Every status change in the service goes through `transition`. Nothing else assigns to
`Order.status`, so the set of legal moves is this table and only this table, and every
move leaves a row in `order_events` with the request id that caused it.
"""

from sqlalchemy.orm import Session

from app.metrics import ORDERS_TOTAL
from app.models import Order, OrderEvent, OrderStatus
from common.errors import AppError
from common.logging import get_logger
from common.request_id import get_request_id

log = get_logger(__name__)

ALLOWED: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.PENDING: frozenset({OrderStatus.RESERVED, OrderStatus.FAILED, OrderStatus.EXPIRED}),
    OrderStatus.RESERVED: frozenset(
        {OrderStatus.CONFIRMED, OrderStatus.FAILED, OrderStatus.EXPIRED}
    ),
    OrderStatus.CONFIRMED: frozenset({OrderStatus.CANCELLED}),
    OrderStatus.FAILED: frozenset(),
    OrderStatus.EXPIRED: frozenset(),
    OrderStatus.CANCELLED: frozenset(),
}

# Statuses whose reason is copied onto the order itself: "why did this fail" is the
# first question asked about one, and digging it out of the event trail every time is work.
_FAILURE_STATUSES = frozenset({OrderStatus.FAILED, OrderStatus.EXPIRED})


class IllegalTransition(AppError):
    code = "ILLEGAL_TRANSITION"
    status_code = 409


def check_transition(order: Order, to_status: OrderStatus) -> None:
    """Raise unless the order may move to `to_status`.

    transition() does this itself. It is separate as well because the cancel flow has to
    know the answer before it asks inventory for the stock back.
    """
    if to_status not in ALLOWED[order.status]:
        raise IllegalTransition(
            f"order {order.id} cannot go from {order.status.value} to {to_status.value}",
            details={
                "order_id": str(order.id),
                "from_status": order.status.value,
                "to_status": to_status.value,
            },
        )


def transition(
    session: Session, order: Order, to_status: OrderStatus, *, reason: str | None = None
) -> OrderEvent:
    """Move an order to `to_status` and record why. Never commits."""
    check_transition(order, to_status)

    from_status = order.status
    event = _record(session, order, from_status, to_status, reason)
    order.status = to_status
    if to_status in _FAILURE_STATUSES:
        order.failure_reason = reason
    log.info(
        "order_transition",
        order_id=str(order.id),
        from_status=from_status.value,
        to_status=to_status.value,
        reason=reason,
    )
    return event


def record_creation(session: Session, order: Order, *, reason: str | None = None) -> OrderEvent:
    """Put a new order into PENDING and open its audit trail.

    There is no transition to check - the order did not exist a moment ago - but the row
    still belongs in the trail. Without it the history of a failed order starts at its
    failure and says nothing about when it was taken.
    """
    order.status = OrderStatus.PENDING
    return _record(session, order, None, OrderStatus.PENDING, reason)


def count(event: OrderEvent) -> None:
    """Count a transition, once the transaction carrying it has committed.

    Kept off the write path deliberately. Counting at transition time also counts moves
    that a rollback then threw away - the loser of an idempotency race, an expiry whose
    commit failed - and orders_total would drift away from the orders_by_status gauge,
    which is read from the table and cannot be wrong. Two panels on one dashboard
    disagreeing is the last thing anyone needs mid incident.
    """
    ORDERS_TOTAL.labels(status=event.to_status.value).inc()


def _record(
    session: Session,
    order: Order,
    from_status: OrderStatus | None,
    to_status: OrderStatus,
    reason: str | None,
) -> OrderEvent:
    event = OrderEvent(
        from_status=from_status,
        to_status=to_status,
        reason=reason,
        request_id=get_request_id(),
    )
    order.events.append(event)
    session.add(order)
    return event
