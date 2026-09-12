"""Two POST /orders carrying the same idempotency key at the same moment.

SQLite serialises writers, so the interesting version of this only means something
against Postgres: both connections are live, both try to insert, and the unique index on
idempotency_key is what decides which of them owns the order.

What the service promises is *not* that both callers get an order back. It is that the
key can only ever buy one order: one caller runs the flow and gets a 201, and the caller
that arrives while that flow is still in the air is told so (409 ORDER_IN_PROGRESS)
rather than being handed a PENDING order dressed up as a completed one. That is the same
answer Stripe gives a key that is still in use, and it is the behaviour this test pins.
"""

import json
import os
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from sqlalchemy import select

from app import service
from app.clients.inventory import get_inventory_client
from app.clients.payments import get_payments_client
from app.db import SessionLocal
from app.models import Order, OrderStatus
from app.schemas import OrderCreate
from common.errors import ConflictError
from tests.conftest import Upstream

POSTGRES = os.environ.get("TEST_DATABASE_URL", "").startswith("postgresql")

# Ceilings, not delays: nothing waits for them unless the race has already gone wrong.
GATE_TIMEOUT_S = 5.0
RESULT_TIMEOUT_S = 20.0


def _gated_reserve(
    entered: threading.Event, may_finish: threading.Event
) -> Callable[[httpx.Request], httpx.Response]:
    """A reservation call that parks until the loser has had its answer.

    Without this the winner could run the whole flow before the second thread even looks
    the key up, and the test would be asserting against a finished order some of the time
    and an in-flight one the rest - green or red depending on how the scheduler felt.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        entered.set()
        may_finish.wait(timeout=GATE_TIMEOUT_S)
        body = json.loads(request.content)
        return httpx.Response(
            201,
            json={"order_id": body["order_id"], "status": "ACTIVE", "items": body["items"]},
        )

    return handler


@pytest.mark.db
@pytest.mark.skipif(not POSTGRES, reason="set TEST_DATABASE_URL to a postgres url")
def test_simultaneous_requests_with_one_key_buy_one_order(upstream: Upstream) -> None:
    key = uuid.uuid4().hex
    payload = {"customer_email": "buyer@example.com", "items": [{"sku": "SKU-0001", "qty": 1}]}
    start = threading.Barrier(2)
    entered_reserve = threading.Event()
    loser_answered = threading.Event()
    upstream.reserve.mock(side_effect=_gated_reserve(entered_reserve, loser_answered))

    def attempt() -> tuple[str, str]:
        # Each thread gets its own session, the way two web workers would.
        with SessionLocal() as session:
            start.wait(timeout=GATE_TIMEOUT_S)
            try:
                order = service.create_order(
                    session,
                    OrderCreate(**payload),
                    key,
                    get_inventory_client(),
                    get_payments_client(),
                )
                return "placed", str(order.id)
            except ConflictError as exc:
                return "conflict", exc.code
            finally:
                # Whichever thread is the loser releases the winner from the gate. The
                # winner only reaches this line after the gate has opened, so it cannot
                # be the one everybody is waiting on.
                loser_answered.set()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(attempt), pool.submit(attempt)]
        outcomes = [f.result(timeout=RESULT_TIMEOUT_S) for f in futures]

    placed = [value for kind, value in outcomes if kind == "placed"]
    refused = [value for kind, value in outcomes if kind == "conflict"]

    # One caller owns the key, the other is told the flow is already running.
    assert len(placed) == 1, outcomes
    assert refused == ["ORDER_IN_PROGRESS"], outcomes
    # The gate proves the refusal happened mid-flight and not after a finished order.
    assert entered_reserve.is_set()

    with SessionLocal() as session:
        rows = session.scalars(select(Order).where(Order.idempotency_key == key)).all()

    assert len(rows) == 1
    assert str(rows[0].id) == placed[0]
    assert rows[0].status is OrderStatus.CONFIRMED
    # The point of all of it: the loser never took stock and never charged a card.
    assert upstream.reserve.call_count == 1
    assert upstream.charge.call_count == 1
