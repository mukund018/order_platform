from collections.abc import Callable
from datetime import timedelta

import pytest
from sqlalchemy.orm import Session

from app import service
from app.clients.payments import get_payments_client
from app.models import Order, OrderStatus, utcnow
from app.service import PAYMENT_TIMEOUT_FAILURE_REASON
from tests.conftest import Upstream

MakeOrder = Callable[..., Order]


@pytest.fixture(autouse=True)
def _clear_payments_client_cache() -> None:
    """get_payments_client() is lru_cache'd, so it must be dropped between tests or a
    respx router from a previous test's `with respx.mock(...)` block stays bound."""
    get_payments_client.cache_clear()
    yield
    get_payments_client.cache_clear()


def _timed_out_order(make_order: MakeOrder, **overrides: object) -> Order:
    order = make_order(status=OrderStatus.FAILED, **overrides)
    order.failure_reason = PAYMENT_TIMEOUT_FAILURE_REASON
    return order


def test_a_charge_that_actually_succeeded_is_counted_as_a_mismatch(
    session: Session, make_order: MakeOrder, upstream: Upstream
) -> None:
    order = _timed_out_order(make_order, unit_price_paise=25000, qty=1)
    session.commit()
    upstream.payment_actually_succeeded(order.id, amount_paise=25000)

    mismatches = service.reconcile_payment_mismatches(session, get_payments_client())

    assert mismatches == 1


def test_the_order_status_is_never_changed_by_reconciliation(
    session: Session, make_order: MakeOrder, upstream: Upstream
) -> None:
    """The reservation this order held may already belong to a different order by the
    time this runs - flipping the status back could silently oversell. Reconciliation
    only ever reports; a human decides what to do about the money."""
    order = _timed_out_order(make_order)
    session.commit()
    upstream.payment_actually_succeeded(order.id)

    service.reconcile_payment_mismatches(session, get_payments_client())

    session.refresh(order)
    assert order.status is OrderStatus.FAILED


def test_a_payment_that_genuinely_never_happened_is_not_a_mismatch(
    session: Session, make_order: MakeOrder, upstream: Upstream
) -> None:
    order = _timed_out_order(make_order)
    session.commit()
    upstream.no_payment_record(order.id)

    mismatches = service.reconcile_payment_mismatches(session, get_payments_client())

    assert mismatches == 0


def test_an_order_failed_for_an_unrelated_reason_is_not_a_candidate(
    session: Session, make_order: MakeOrder, upstream: Upstream
) -> None:
    order = make_order(status=OrderStatus.FAILED)
    order.failure_reason = "insufficient_funds"
    session.commit()
    # No route configured for this order id at all - if it were (wrongly) treated as a
    # candidate, the respx call would raise for an unmocked route rather than silently pass.

    mismatches = service.reconcile_payment_mismatches(session, get_payments_client())

    assert mismatches == 0


def test_an_order_outside_the_lookback_window_is_not_checked(
    session: Session, make_order: MakeOrder, upstream: Upstream
) -> None:
    _timed_out_order(
        make_order, created_at=utcnow() - service.RECONCILE_LOOKBACK - timedelta(hours=1)
    )
    session.commit()
    # Same reasoning as above: no route registered for `old`, so if it were checked
    # anyway this test would fail on the unmocked request rather than on a wrong count.

    mismatches = service.reconcile_payment_mismatches(session, get_payments_client())

    assert mismatches == 0


def test_a_confirmed_mismatch_is_still_reported_alongside_unrelated_failures(
    session: Session, make_order: MakeOrder, upstream: Upstream
) -> None:
    mismatch = _timed_out_order(make_order)
    genuinely_declined = make_order(status=OrderStatus.FAILED)
    genuinely_declined.failure_reason = "card_declined"
    session.commit()
    upstream.payment_actually_succeeded(mismatch.id)

    mismatches = service.reconcile_payment_mismatches(session, get_payments_client())

    assert mismatches == 1
