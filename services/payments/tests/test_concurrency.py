"""Two requests for one order arriving at the same moment.

SQLite serialises writers, so the interesting version of this only means something
against Postgres: both connections are live, both try to insert, and the unique index
on order_id is what decides the winner.
"""

import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

from app import service
from app.db import SessionLocal
from app.models import Payment
from common.errors import ConflictError
from tests.conftest import StubGateway

POSTGRES = os.environ.get("TEST_DATABASE_URL", "").startswith("postgresql")


@pytest.mark.db
@pytest.mark.skipif(not POSTGRES, reason="set TEST_DATABASE_URL to a postgres url")
def test_simultaneous_charges_insert_one_payment(order_id: uuid.UUID) -> None:
    gateway = StubGateway()
    start = threading.Barrier(2)

    def attempt() -> str:
        with SessionLocal() as session:
            start.wait(timeout=5)
            try:
                return service.charge(session, order_id, 90_000, gateway).status.value
            except ConflictError as exc:
                return exc.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(attempt), pool.submit(attempt)]
        outcomes = sorted(future.result(timeout=15) for future in futures)

    with SessionLocal() as session:
        rows = session.query(Payment).filter_by(order_id=order_id).all()

    assert len(rows) == 1
    assert len(gateway.calls) == 1
    assert "SUCCEEDED" in outcomes
