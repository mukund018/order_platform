"""Order orchestration.

One HTTP request here touches three databases through two other services, and any of
those steps can fail or simply not answer. The rules the rest of this module follows:

  * the order row is committed before the first outbound call, so a crash mid flight
    leaves an order to clean up rather than money moved against nothing;
  * no request holds a transaction open across an outbound call. A call can take seconds,
    and a connection held that long starves the pool for every other request on the box;
  * a business "no" (out of stock, card declined) ends as a FAILED order, not an HTTP
    error - the request was handled correctly, the answer was no;
  * a reservation taken and not paid for is always released, including when the payment
    call is the thing that blew up.
"""

import uuid
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from kombu.exceptions import OperationalError as BrokerError
from sqlalchemy import Select, case, func, select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from app import state
from app.clients.inventory import InventoryClient, ReservationLine
from app.clients.payments import PaymentsClient
from app.config import get_settings
from app.metrics import PAYMENT_RECONCILIATION_MISMATCH
from app.models import (
    UNFINISHED_STATUSES,
    Notification,
    NotificationKind,
    Order,
    OrderEvent,
    OrderItem,
    OrderStatus,
    utcnow,
)
from app.schemas import DailyReport, OrderCreate
from common.errors import AppError, ConflictError, NotFoundError
from common.logging import get_logger

log = get_logger(__name__)

# Inventory answers an unknown sku with NOT_FOUND when pricing and UNKNOWN_SKU when
# reserving. Both mean the same thing to us.
UNKNOWN_SKU_CODES = frozenset({"UNKNOWN_SKU", "NOT_FOUND"})

# Reservation answers that are a decision, not a fault. Anything else (a timeout, a
# 5xx) leaves the order PENDING for the expiry job, because we do not know whether the
# stock was taken.
RESERVATION_REFUSALS = frozenset({"OUT_OF_STOCK", "UNKNOWN_SKU"})

# Upper bound on one expiry run, so a backlog is worked off over several ticks instead
# of one task running for minutes.
EXPIRY_BATCH = 200

# How long the expiry job keeps retrying a release inventory will not accept before it
# closes the order out anyway. Past this the stock is not coming back on its own, and an
# order parked in PENDING only hides that from the people looking at order statuses.
RELEASE_GIVE_UP = timedelta(hours=6)

# INC-004: the exact message _fail stores when a payment call raises UpstreamTimeoutError
# (see clients/base.py). There is no separate error-code column on orders to filter on
# instead - this string is the only queryable signal, and it is only as safe as this
# constant staying in sync with base.py's message. Worth a proper failure_code column
# if this class of fault recurs.
PAYMENT_TIMEOUT_FAILURE_REASON = "payments did not answer within the timeout"

# How far back reconciliation looks each run. Wide enough to catch anything the previous
# run's window edge might have missed, cheap enough to run often - this queries orders,
# not payments, and calls out to payments-service once per candidate, not once per order
# in the window.
RECONCILE_LOOKBACK = timedelta(hours=24)
RECONCILE_BATCH = 200


def create_order(
    session: Session,
    payload: OrderCreate,
    idempotency_key: str,
    inventory: InventoryClient,
    payments: PaymentsClient,
) -> Order:
    existing = find_by_idempotency_key(session, idempotency_key)
    # That lookup opened a transaction, and everything below it is HTTP. Ending it here
    # hands the connection back to the pool instead of holding one idle for the length of
    # the flow; expire_on_commit=False leaves anything already loaded readable.
    session.commit()
    if existing is not None:
        return _replay(existing, payload, idempotency_key)

    order, created = _insert_pending(session, payload, idempotency_key, inventory)
    if not created:
        # Another request owns this key and is running the flow for it right now.
        return _replay(order, payload, idempotency_key)

    if _reserve(session, order, inventory):
        _settle(session, order, inventory, payments)
    return order


def get_order(session: Session, order_id: uuid.UUID) -> Order:
    order = session.scalar(_with_children(select(Order).where(Order.id == order_id)))
    if order is None:
        raise NotFoundError(f"no order with id {order_id}", details={"order_id": str(order_id)})
    return order


def list_orders(
    session: Session,
    *,
    status: OrderStatus | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[Order]:
    # created_at alone is not unique - a burst of orders shares a timestamp - and a
    # non-unique sort lets a row appear on two pages or on none.
    statement = (
        select(Order).order_by(Order.created_at.desc(), Order.id.desc()).limit(limit).offset(offset)
    )
    if status is not None:
        statement = statement.where(Order.status == status)
    return list(session.scalars(statement))


def find_by_idempotency_key(session: Session, idempotency_key: str) -> Order | None:
    return session.scalar(
        _with_children(select(Order).where(Order.idempotency_key == idempotency_key))
    )


def cancel_order(session: Session, order_id: uuid.UUID, inventory: InventoryClient) -> Order:
    order = get_order(session, order_id)
    # Ask first. The transition is the step that can legally refuse, and handing the stock
    # back before discovering the order cannot be cancelled is the worse way round to fail.
    state.check_transition(order, OrderStatus.CANCELLED)
    # The read above opened a transaction; it ends here rather than around the release.
    session.commit()

    # Unlike every other release in this module this one is allowed to blow up: the
    # customer asked for the cancellation, so telling them it did not happen and letting
    # them ask again beats reporting a cancellation that never returned the stock.
    inventory.release(order.id)

    event = state.transition(session, order, OrderStatus.CANCELLED, reason="cancelled by customer")
    _commit(session, event)
    log.info("order_cancelled", order_id=str(order.id), total_paise=order.total_paise)
    return order


def send_confirmation(session: Session, order_id: uuid.UUID) -> Notification | None:
    """Record the confirmation mail. Returns None when there is nothing to send."""
    order = session.get(Order, order_id)
    if order is None:
        raise NotFoundError(f"no order with id {order_id}", details={"order_id": str(order_id)})
    if order.status is not OrderStatus.CONFIRMED:
        # A cancellation that overtook the queued task. The mail correctly does not go
        # out, and nothing is wrong, so this must not count as a failed task - a handful
        # of them would trip the celery failure alert for a race that is working.
        log.warning("confirmation_skipped", order_id=str(order_id), order_status=order.status.value)
        return None

    notification = Notification(order_id=order.id, kind=NotificationKind.CONFIRMATION)
    session.add(notification)
    try:
        session.commit()
    except IntegrityError:
        # The unique index on (order_id, kind) is what makes a retried task safe. Losing
        # the insert is the correct outcome, not an error.
        session.rollback()
        log.info("confirmation_already_sent", order_id=str(order_id))
        return None

    log.info("confirmation_sent", order_id=str(order_id), notification_id=str(notification.id))
    return notification


def expire_stale_orders(
    session: Session, inventory: InventoryClient, *, now: datetime | None = None
) -> int:
    """Give back stock held by orders that never finished. Commits one order at a time."""
    minutes = get_settings().order_expiry_minutes
    moment = now or utcnow()
    cutoff = moment - timedelta(minutes=minutes)
    stale = session.scalars(
        select(Order.id)
        .where(Order.status.in_(UNFINISHED_STATUSES), Order.created_at < cutoff)
        .order_by(Order.created_at)
        .limit(EXPIRY_BATCH)
    ).all()
    # Ids, and the read transaction closed, before the loop: a batch of this size is
    # mostly time spent waiting on inventory, and none of it should be spent on a
    # connection somebody else could be using.
    session.commit()

    expired = 0
    for order_id in stale:
        try:
            if _expire_one(session, order_id, inventory, minutes=minutes, now=moment):
                expired += 1
        except (AppError, SQLAlchemyError) as exc:
            # One row that cannot be expired must not hold up the rest of the batch.
            session.rollback()
            log.error("order_expiry_failed", order_id=str(order_id), error=str(exc))
            continue

    if expired:
        log.info("orders_expired", count=expired, cutoff=cutoff.isoformat())
    return expired


def _expire_one(
    session: Session,
    order_id: uuid.UUID,
    inventory: InventoryClient,
    *,
    minutes: int,
    now: datetime,
) -> bool:
    """Expire one order in its own transaction. False means it was left for a later tick.

    The row is locked for the length of its release call, which is the one place in this
    module that does hold a transaction over an outbound call. It is worth it here: beat
    queues this job every minute and a backlog run takes longer than that, so two runs do
    overlap, and without the lock both expire the same order and the transition lands in
    the audit trail twice. The cost is bounded by INVENTORY_TIMEOUT_S and one row.
    """
    order = session.scalar(
        select(Order).where(Order.id == order_id).with_for_update(skip_locked=True)
    )
    if order is None or order.status not in UNFINISHED_STATUSES:
        return False

    reason = f"not completed within {minutes}m"
    if not _release(order, inventory):
        if order.created_at > now - RELEASE_GIVE_UP:
            # EXPIRED is terminal and the query above only looks at unfinished orders, so
            # expiring now would strand the reservation with nothing left to revisit it.
            # Inventory's release is idempotent, so waiting for the next tick is free.
            return False
        reason = f"{reason}, stock not released"

    event = state.transition(session, order, OrderStatus.EXPIRED, reason=reason)
    _commit(session, event)
    return True


def reconcile_payment_mismatches(
    session: Session, payments: PaymentsClient, *, now: datetime | None = None
) -> int:
    """INC-004: find orders marked FAILED because payments timed out, and check
    whether payments-service actually completed the charge anyway - a caller timeout
    only means *we* stopped waiting, not that the request on the other end stopped.

    Deliberately does not touch the order's status. By the time this runs, the
    reservation this order held has long since been released and may already belong to
    a different order - flipping this one back to CONFIRMED could silently oversell.
    The correct action (refund, manual fulfilment, goodwill credit) depends on facts
    this function cannot see, so it surfaces the mismatch loudly instead of guessing:
    a log line with the amount and provider_ref, and a metric an alert can watch.
    """
    moment = now or utcnow()
    cutoff = moment - RECONCILE_LOOKBACK
    candidates = session.scalars(
        select(Order.id)
        .where(
            Order.status == OrderStatus.FAILED,
            Order.failure_reason == PAYMENT_TIMEOUT_FAILURE_REASON,
            Order.created_at >= cutoff,
        )
        .order_by(Order.created_at)
        .limit(RECONCILE_BATCH)
    ).all()

    mismatches = 0
    for order_id in candidates:
        payment = payments.get_status(order_id)
        if payment is not None and payment.succeeded:
            mismatches += 1
            PAYMENT_RECONCILIATION_MISMATCH.inc()
            log.error(
                "payment_reconciliation_mismatch",
                order_id=str(order_id),
                amount_paise=payment.amount_paise,
                provider_ref=payment.provider_ref,
            )

    if mismatches:
        log.warning("payment_reconciliation_run", checked=len(candidates), mismatches=mismatches)
    return mismatches


def business_day_bounds(day: date, timezone: str) -> tuple[datetime, datetime]:
    """The UTC half-open range [start, end) covering one calendar day in `timezone`.

    The store runs on IST and the database stores UTC. 00:30 IST on the 3rd is 19:00 UTC
    on the 2nd, so anything that slices on the UTC date bills a chunk of every evening
    to the wrong business day.
    """
    zone = ZoneInfo(timezone)
    start = datetime.combine(day, time.min, tzinfo=zone)
    # The next local midnight, not start + 24h: in a zone that observes DST those are
    # not the same instant.
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone)
    return start.astimezone(UTC), end.astimezone(UTC)


def previous_business_day(timezone: str) -> date:
    """The business day that just ended.

    Beat fires the report at 00:05 local time, so the day to report on is the one before
    the local date - which around midnight is not the same as the day before in UTC.
    """
    return datetime.now(ZoneInfo(timezone)).date() - timedelta(days=1)


def daily_report(session: Session, day: date) -> DailyReport:
    timezone = get_settings().business_timezone
    start, end = business_day_bounds(day, timezone)

    confirmed = case((Order.status == OrderStatus.CONFIRMED, 1), else_=0)
    revenue = case((Order.status == OrderStatus.CONFIRMED, Order.total_paise), else_=0)
    orders, confirmed_orders, revenue_paise = session.execute(
        select(
            func.count(Order.id),
            func.coalesce(func.sum(confirmed), 0),
            func.coalesce(func.sum(revenue), 0),
        ).where(Order.created_at >= start, Order.created_at < end)
    ).one()

    return DailyReport(
        date=day,
        timezone=timezone,
        window_start=start,
        window_end=end,
        orders=orders,
        confirmed_orders=confirmed_orders,
        revenue_paise=revenue_paise,
    )


def _replay(order: Order, payload: OrderCreate, idempotency_key: str) -> Order:
    """Answer a key we have already placed an order for."""
    if not _same_request(order, payload):
        # Keys are unique across the whole table and the caller picks them, so two
        # customers can collide on one. Handing back the other customer's order - id,
        # address, items and all - as a 201 would read to both of them as success.
        raise ConflictError(
            f"idempotency key {idempotency_key} was used for a different order",
            code="IDEMPOTENCY_KEY_REUSED",
            details={"order_id": str(order.id)},
        )

    if order.status in UNFINISHED_STATUSES:
        # The first attempt never finished, and it may even be running right now. Picking
        # the flow up here would put two requests on the same reservation and the same
        # charge; returning a 201 that says PENDING would tell the caller their order was
        # placed when nothing has been reserved or paid for. Neither is honest, so the
        # caller is told to look at the order instead.
        raise ConflictError(
            f"order {order.id} placed with this key is still {order.status.value}",
            code="ORDER_IN_PROGRESS",
            details={"order_id": str(order.id), "status": order.status.value},
        )

    log.info(
        "order_replayed",
        order_id=str(order.id),
        status=order.status.value,
        idempotency_key=idempotency_key,
    )
    return order


def _same_request(order: Order, payload: OrderCreate) -> bool:
    """Does this body describe the order the key already placed?"""
    placed = sorted((item.sku, item.qty) for item in order.items)
    asked = sorted((item.sku, item.qty) for item in payload.items)
    return order.customer_email == payload.customer_email and placed == asked


def _insert_pending(
    session: Session,
    payload: OrderCreate,
    idempotency_key: str,
    inventory: InventoryClient,
) -> tuple[Order, bool]:
    """Insert the PENDING order, or lose the race on the key and return the winner."""
    items = _price_lines(payload, inventory)
    order = Order(
        customer_email=payload.customer_email,
        idempotency_key=idempotency_key,
        total_paise=sum(item.qty * item.unit_price_paise for item in items),
        items=items,
    )
    event = state.record_creation(session, order, reason="order placed")

    try:
        # Committing here, before the first outbound call, is deliberate: if the process
        # dies half way through the flow the order still exists as PENDING and the expiry
        # job can release whatever it holds. Doing the work first and writing at the end
        # would lose the order entirely.
        session.commit()
    except IntegrityError:
        session.rollback()
        winner = find_by_idempotency_key(session, idempotency_key)
        if winner is None:
            raise
        log.info("order_insert_raced", order_id=str(winner.id), idempotency_key=idempotency_key)
        return winner, False

    state.count(event)
    log.info(
        "order_created",
        order_id=str(order.id),
        total_paise=order.total_paise,
        items=len(items),
    )
    return order, True


def _price_lines(payload: OrderCreate, inventory: InventoryClient) -> list[OrderItem]:
    """Copy today's catalogue price onto the order, so the total cannot change later."""
    try:
        products = inventory.get_products([item.sku for item in payload.items])
    except AppError as exc:
        if exc.code in UNKNOWN_SKU_CODES:
            raise NotFoundError(exc.message, code="UNKNOWN_SKU", details=exc.details) from exc
        raise

    return [
        OrderItem(
            product_id=products[item.sku].id,
            sku=item.sku,
            qty=item.qty,
            unit_price_paise=products[item.sku].price_paise,
        )
        for item in payload.items
    ]


def _reserve(session: Session, order: Order, inventory: InventoryClient) -> bool:
    lines = [ReservationLine(sku=item.sku, qty=item.qty) for item in order.items]
    try:
        inventory.reserve(order.id, lines)
    except AppError as exc:
        if exc.code not in RESERVATION_REFUSALS:
            raise
        event = state.transition(session, order, OrderStatus.FAILED, reason=exc.message)
        _commit(session, event)
        log.warning(
            "order_failed",
            order_id=str(order.id),
            stage="reservation",
            error_code=exc.code,
            reason=exc.message,
        )
        return False

    event = state.transition(session, order, OrderStatus.RESERVED, reason="stock reserved")
    _commit(session, event)
    return True


def _settle(
    session: Session, order: Order, inventory: InventoryClient, payments: PaymentsClient
) -> None:
    try:
        payment = payments.charge(order.id, order.total_paise)
    except AppError as exc:
        _fail(session, order, inventory, reason=exc.message, error_code=exc.code)
        return

    if not payment.succeeded:
        _fail(
            session,
            order,
            inventory,
            reason=payment.failure_reason or "payment declined",
            error_code="PAYMENT_DECLINED",
        )
        return

    _commit_reservation(order, inventory)
    event = state.transition(
        session, order, OrderStatus.CONFIRMED, reason=f"paid, provider ref {payment.provider_ref}"
    )
    _commit(session, event)
    _enqueue_confirmation(order)


def _fail(
    session: Session,
    order: Order,
    inventory: InventoryClient,
    *,
    reason: str,
    error_code: str,
) -> None:
    # A release inventory refuses is logged and nothing more. The order has to fail either
    # way - the customer has been told no - and unlike the expiry job there is no later
    # tick to try it again on.
    _release(order, inventory)
    event = state.transition(session, order, OrderStatus.FAILED, reason=reason)
    _commit(session, event)
    log.warning(
        "order_failed",
        order_id=str(order.id),
        stage="payment",
        error_code=error_code,
        reason=reason,
    )


def _release(order: Order, inventory: InventoryClient) -> bool:
    """Compensation: hand back stock the order is never going to pay for.

    False means inventory refused. It must not mask the failure that got us here, so it
    is never raised from - it is logged loudly and the caller decides whether it is worth
    stopping for. Stock sitting reserved against an order that will never complete is
    what somebody eventually gets paged about.
    """
    try:
        inventory.release(order.id)
    except AppError as exc:
        log.error(
            "reservation_release_failed",
            order_id=str(order.id),
            error_code=exc.code,
            reason=exc.message,
        )
        return False
    return True


def _commit_reservation(order: Order, inventory: InventoryClient) -> None:
    try:
        inventory.commit(order.id)
    except AppError as exc:
        # The card has already been charged. Releasing the stock would be wrong and
        # failing the order would lose the payment, so the order stands and the stuck
        # reservation is left for a human to reconcile against inventory.
        log.error(
            "reservation_commit_failed",
            order_id=str(order.id),
            error_code=exc.code,
            reason=exc.message,
        )


def _enqueue_confirmation(order: Order) -> None:
    # Imported here rather than at the top because tasks.py imports this module.
    from app import tasks

    try:
        tasks.send_confirmation.delay(str(order.id))
    except BrokerError as exc:
        # The order is confirmed either way; the customer just does not get the mail.
        log.error("confirmation_enqueue_failed", order_id=str(order.id), error=str(exc))


def _commit(session: Session, event: OrderEvent) -> None:
    """Make a transition durable and only then count it, never the other way round."""
    session.commit()
    state.count(event)


def _with_children(statement: Select[tuple[Order]]) -> Select[tuple[Order]]:
    """Eager-load items and events; every caller of this serialises both."""
    return statement.options(selectinload(Order.items), selectinload(Order.events))
