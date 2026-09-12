import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from celery.exceptions import Retry
from freezegun import freeze_time
from sqlalchemy import func, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app import service
from app.clients.inventory import get_inventory_client
from app.models import Notification, Order, OrderStatus, utcnow
from app.schemas import DailyReport
from app.tasks import daily_sales_report, expire_stale_orders, send_confirmation
from common.errors import ConflictError, NotFoundError
from tests.conftest import Upstream, error_body

STALE = timedelta(minutes=20)


def notifications(session: Session) -> int:
    return session.scalar(select(func.count(Notification.id))) or 0


def inventory_is_unwell() -> httpx.Response:
    return httpx.Response(500, json=error_body("INTERNAL_ERROR", "inventory is unwell"))


def run_as_worker(order_id: str) -> None:
    """Run the task the way a worker does.

    Called directly - and apply() looks the same to Task.retry - celery re-raises the
    original exception instead of retrying, so the retry configuration would never be
    exercised.
    """
    send_confirmation.push_request(is_eager=True, called_directly=False, retries=0)
    try:
        send_confirmation.run(order_id)
    finally:
        send_confirmation.pop_request()


def reports(monkeypatch: pytest.MonkeyPatch) -> list[DailyReport]:
    """Capture what the report task computed; the task itself only logs it."""
    captured: list[DailyReport] = []
    real = service.daily_report

    def spy(session: Session, day: date) -> DailyReport:
        report = real(session, day)
        captured.append(report)
        return report

    monkeypatch.setattr(service, "daily_report", spy)
    return captured


def test_a_confirmation_is_written_once_however_often_the_task_runs(
    session: Session, make_order: Callable[..., Order]
) -> None:
    order = make_order(status=OrderStatus.CONFIRMED)

    first = service.send_confirmation(session, order.id)
    second = service.send_confirmation(session, order.id)

    assert first is not None
    # The second run loses the insert on the unique index, which is the whole point.
    assert second is None
    assert notifications(session) == 1


def test_a_confirmation_for_an_order_that_is_no_longer_confirmed_is_skipped(
    session: Session, make_order: Callable[..., Order]
) -> None:
    order = make_order(status=OrderStatus.CANCELLED)

    # A cancellation that overtook the queued task. No mail, and no failed task either:
    # counting this as a failure would page somebody for a race that worked.
    assert service.send_confirmation(session, order.id) is None
    assert notifications(session) == 0


def test_a_confirmation_for_an_order_that_does_not_exist(session: Session) -> None:
    with pytest.raises(NotFoundError):
        service.send_confirmation(session, uuid.uuid4())


def test_the_confirmation_task_runs_the_domain_function(
    session: Session, make_order: Callable[..., Order]
) -> None:
    order = make_order(status=OrderStatus.CONFIRMED)

    send_confirmation(str(order.id))

    assert notifications(session) == 1


def test_the_confirmation_task_retries_when_the_database_is_unreachable(
    make_order: Callable[..., Order], monkeypatch: pytest.MonkeyPatch
) -> None:
    order = make_order(status=OrderStatus.CONFIRMED)

    def unreachable(*_args: object, **_kwargs: object) -> None:
        raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    monkeypatch.setattr(service, "send_confirmation", unreachable)

    with pytest.raises(Retry) as caught:
        run_as_worker(str(order.id))

    assert isinstance(caught.value.exc, OperationalError)
    assert send_confirmation.max_retries == 5


def test_the_confirmation_task_does_not_retry_what_will_never_work(
    session: Session, make_order: Callable[..., Order]
) -> None:
    # Only a database that is momentarily away is worth backing off for. An order that
    # is not there is not going to appear on the fifth attempt.
    with pytest.raises(NotFoundError):
        run_as_worker(str(uuid.uuid4()))

    order = make_order(status=OrderStatus.CANCELLED)
    run_as_worker(str(order.id))

    assert notifications(session) == 0


def test_an_old_order_is_expired_and_its_stock_released(
    session: Session, make_order: Callable[..., Order], upstream: Upstream
) -> None:
    old = make_order(status=OrderStatus.RESERVED, created_at=utcnow() - STALE)
    fresh = make_order(status=OrderStatus.PENDING)

    expired = service.expire_stale_orders(session, get_inventory_client())

    assert expired == 1
    session.refresh(old)
    session.refresh(fresh)
    assert old.status is OrderStatus.EXPIRED
    assert old.failure_reason == "not completed within 15m"
    assert fresh.status is OrderStatus.PENDING
    assert upstream.release.call_count == 1


def test_expiry_leaves_finished_orders_alone(
    session: Session, make_order: Callable[..., Order], upstream: Upstream
) -> None:
    for status in (OrderStatus.CONFIRMED, OrderStatus.FAILED, OrderStatus.CANCELLED):
        make_order(status=status, created_at=utcnow() - STALE)

    assert service.expire_stale_orders(session, get_inventory_client()) == 0
    assert upstream.release.call_count == 0


def test_an_order_whose_stock_cannot_be_released_waits_for_the_next_tick(
    session: Session, make_order: Callable[..., Order], upstream: Upstream
) -> None:
    upstream.release.mock(return_value=inventory_is_unwell())
    order = make_order(status=OrderStatus.RESERVED, created_at=utcnow() - STALE)

    assert service.expire_stale_orders(session, get_inventory_client()) == 0

    session.refresh(order)
    # EXPIRED is terminal and the expiry query only looks at unfinished orders, so
    # expiring now would leave the reservation ACTIVE with nothing left to revisit it.
    assert order.status is OrderStatus.RESERVED

    upstream.release.mock(
        return_value=httpx.Response(
            200, json={"order_id": str(order.id), "status": "RELEASED", "items": []}
        )
    )
    assert service.expire_stale_orders(session, get_inventory_client()) == 1

    session.refresh(order)
    assert order.status is OrderStatus.EXPIRED


def test_an_order_whose_stock_never_comes_back_is_closed_out_in_the_end(
    session: Session, make_order: Callable[..., Order], upstream: Upstream
) -> None:
    upstream.release.mock(return_value=inventory_is_unwell())
    order = make_order(
        status=OrderStatus.RESERVED,
        created_at=utcnow() - service.RELEASE_GIVE_UP - STALE,
    )

    assert service.expire_stale_orders(session, get_inventory_client()) == 1

    session.refresh(order)
    # Retrying the same release every minute for ever hides the problem. The order is
    # closed and its reason says the stock needs reconciling by hand.
    assert order.status is OrderStatus.EXPIRED
    assert order.failure_reason == "not completed within 15m, stock not released"


def test_an_order_another_run_already_expired_is_left_alone(
    session: Session, make_order: Callable[..., Order], upstream: Upstream
) -> None:
    first = make_order(status=OrderStatus.RESERVED, created_at=utcnow() - STALE - STALE)
    second = make_order(status=OrderStatus.RESERVED, created_at=utcnow() - STALE)

    def expire_the_other_one(request: httpx.Request) -> httpx.Response:
        session.execute(
            update(Order).where(Order.id == second.id).values(status=OrderStatus.EXPIRED)
        )
        session.commit()
        return httpx.Response(
            200, json={"order_id": str(first.id), "status": "RELEASED", "items": []}
        )

    # Beat queues this job every minute and a backlog run takes longer than that, so the
    # other run can finish an order that is already in this run's batch. Writing the move
    # twice would put the same transition in the audit trail twice.
    upstream.release.mock(side_effect=expire_the_other_one)

    assert service.expire_stale_orders(session, get_inventory_client()) == 1

    assert upstream.release.call_count == 1
    assert second.events == []


def test_one_order_that_cannot_be_expired_does_not_hold_up_the_rest(
    session: Session,
    make_order: Callable[..., Order],
    upstream: Upstream,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doomed = make_order(status=OrderStatus.PENDING, created_at=utcnow() - STALE)
    make_order(status=OrderStatus.PENDING, created_at=utcnow() - STALE)
    real_transition = service.state.transition

    def explode_for_one(
        db: Session, order: Order, to_status: OrderStatus, *, reason: str | None = None
    ) -> object:
        if order.id == doomed.id:
            raise ConflictError("this row is having a bad day")
        return real_transition(db, order, to_status, reason=reason)

    monkeypatch.setattr(service.state, "transition", explode_for_one)

    assert service.expire_stale_orders(session, get_inventory_client()) == 1


def test_the_expiry_task_runs_the_domain_function(
    session: Session, make_order: Callable[..., Order], upstream: Upstream
) -> None:
    order = make_order(status=OrderStatus.PENDING, created_at=utcnow() - STALE)

    expire_stale_orders()

    session.refresh(order)
    assert order.status is OrderStatus.EXPIRED


def test_the_daily_report_task_reports_the_day_it_was_given(
    session: Session, make_order: Callable[..., Order], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Inside the IST day of the 12th, which runs from 18:30 UTC on the 11th.
    make_order(
        status=OrderStatus.CONFIRMED,
        created_at=datetime(2025, 11, 12, 6, 0, tzinfo=UTC),
        unit_price_paise=10000,
    )
    make_order(status=OrderStatus.CONFIRMED, created_at=datetime(2025, 11, 9, 6, 0, tzinfo=UTC))
    captured = reports(monkeypatch)

    daily_sales_report("2025-11-12")

    assert len(captured) == 1
    assert captured[0].date == date(2025, 11, 12)
    assert captured[0].confirmed_orders == 1
    assert captured[0].revenue_paise == 10000


@freeze_time("2025-11-12T19:30:00Z")
def test_the_daily_report_task_defaults_to_the_ist_day_that_just_ended(
    session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = reports(monkeypatch)

    # How beat calls it, at 00:05 IST. 19:30 UTC is 01:00 IST on the 13th, so the day
    # that just ended is the 12th - still today by the UTC calendar.
    daily_sales_report()

    assert captured[0].date == date(2025, 11, 12)
