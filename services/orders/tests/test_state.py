from collections.abc import Callable

import pytest
from prometheus_client import REGISTRY
from sqlalchemy.orm import Session

from app import state
from app.models import Order, OrderStatus
from app.state import ALLOWED, IllegalTransition
from common.request_id import bind_request_id, clear_context

# The state machine as the spec draws it, written out by hand. Generating the cases from
# app.state.ALLOWED - the table under test - would only prove that transition() agrees
# with whatever the table happens to say, and an extra edge added to it by mistake would
# still leave every case here green.
EXPECTED: dict[OrderStatus, set[OrderStatus]] = {
    OrderStatus.PENDING: {OrderStatus.RESERVED, OrderStatus.FAILED, OrderStatus.EXPIRED},
    OrderStatus.RESERVED: {OrderStatus.CONFIRMED, OrderStatus.FAILED, OrderStatus.EXPIRED},
    OrderStatus.CONFIRMED: {OrderStatus.CANCELLED},
    OrderStatus.FAILED: set(),
    OrderStatus.EXPIRED: set(),
    OrderStatus.CANCELLED: set(),
}

LEGAL = [(source, target) for source, targets in EXPECTED.items() for target in targets]
ILLEGAL = [
    (source, target)
    for source in OrderStatus
    for target in OrderStatus
    if target not in EXPECTED[source]
]


def orders_counted(status: OrderStatus) -> float:
    return REGISTRY.get_sample_value("orders_total", {"status": status.value}) or 0.0


def test_the_table_is_the_state_machine_the_spec_describes() -> None:
    assert {source: frozenset(targets) for source, targets in EXPECTED.items()} == ALLOWED


@pytest.mark.parametrize(("source", "target"), LEGAL)
def test_a_legal_transition_moves_the_order_and_writes_an_event(
    session: Session,
    make_order: Callable[..., Order],
    source: OrderStatus,
    target: OrderStatus,
) -> None:
    order = make_order(status=source)

    event = state.transition(session, order, target, reason="because")
    session.commit()

    assert order.status is target
    assert event.from_status is source
    assert event.to_status is target
    assert event.reason == "because"
    assert order.events == [event]


@pytest.mark.parametrize(("source", "target"), ILLEGAL)
def test_an_illegal_transition_raises_and_changes_nothing(
    session: Session,
    make_order: Callable[..., Order],
    source: OrderStatus,
    target: OrderStatus,
) -> None:
    order = make_order(status=source)

    with pytest.raises(IllegalTransition) as caught:
        state.transition(session, order, target)

    assert caught.value.code == "ILLEGAL_TRANSITION"
    assert caught.value.status_code == 409
    assert caught.value.details["from_status"] == source.value
    assert order.status is source
    assert order.events == []


def test_a_transition_is_counted_only_when_the_caller_says_it_committed(
    session: Session, make_order: Callable[..., Order]
) -> None:
    order = make_order(status=OrderStatus.PENDING)
    before = orders_counted(OrderStatus.RESERVED)

    event = state.transition(session, order, OrderStatus.RESERVED, reason="stock reserved")

    # Counting here would count rollbacks too, and orders_total cannot be corrected.
    assert orders_counted(OrderStatus.RESERVED) == before

    state.count(event)

    assert orders_counted(OrderStatus.RESERVED) == before + 1


def test_a_check_refuses_without_touching_the_order(
    session: Session, make_order: Callable[..., Order]
) -> None:
    order = make_order(status=OrderStatus.PENDING)

    with pytest.raises(IllegalTransition):
        state.check_transition(order, OrderStatus.CANCELLED)

    assert order.status is OrderStatus.PENDING
    assert order.events == []


def test_a_failure_reason_is_kept_on_the_order(
    session: Session, make_order: Callable[..., Order]
) -> None:
    order = make_order(status=OrderStatus.PENDING)

    state.transition(session, order, OrderStatus.FAILED, reason="card declined")

    assert order.failure_reason == "card declined"


def test_a_success_reason_stays_in_the_event_only(
    session: Session, make_order: Callable[..., Order]
) -> None:
    order = make_order(status=OrderStatus.PENDING)

    event = state.transition(session, order, OrderStatus.RESERVED, reason="stock reserved")

    assert order.failure_reason is None
    assert event.reason == "stock reserved"


def test_the_event_carries_the_request_id_that_caused_it(
    session: Session, make_order: Callable[..., Order]
) -> None:
    order = make_order(status=OrderStatus.PENDING)
    request_id = bind_request_id("abc123")
    try:
        event = state.transition(session, order, OrderStatus.RESERVED)
    finally:
        clear_context()

    assert event.request_id == request_id


def test_an_event_outside_a_request_has_no_request_id(
    session: Session, make_order: Callable[..., Order]
) -> None:
    clear_context()
    order = make_order(status=OrderStatus.PENDING)

    event = state.transition(session, order, OrderStatus.EXPIRED, reason="too old")

    assert event.request_id is None


def test_creation_is_recorded_as_an_event_with_no_previous_status(session: Session) -> None:
    order = Order(
        customer_email="buyer@example.com",
        idempotency_key="key-creation",
        total_paise=100,
    )

    event = state.record_creation(session, order, reason="order placed")
    session.commit()

    assert order.status is OrderStatus.PENDING
    assert event.from_status is None
    assert event.to_status is OrderStatus.PENDING
    assert order.events == [event]
