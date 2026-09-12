"""The oversell test from M1. Postgres only.

SQLite proves nothing here: pysqlite takes a database-wide write lock, so the threads
would take stock one at a time and the test would pass even against a naive
read-subtract-write. Only a real server gives each thread its own transaction against
the same row, which is the case the conditional UPDATE in service.reserve exists for.
"""

import os
import uuid
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app import service
from app.db import engine
from app.models import Product, ReservationStatus, StockReservation
from app.schemas import ReservationItem
from common.errors import OutOfStockError

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(
        not os.environ.get("TEST_DATABASE_URL"),
        reason="oversell can only be shown against a live postgres: set TEST_DATABASE_URL",
    ),
]

STOCK = 10
THREADS = 20
# A thread that dies before reaching the barrier would hang the other nineteen.
BARRIER_TIMEOUT_S = 30.0


@pytest.fixture
def threaded_sessions() -> Iterator[sessionmaker[Session]]:
    """A session factory with one connection per thread.

    The app engine is sized 5 + 5, so on that pool half the threads would queue for a
    connection and arrive at the row one wave after the other. That is not the collision
    this test is trying to produce.
    """
    concurrent_engine = create_engine(engine.url, pool_size=THREADS, max_overflow=0, future=True)
    try:
        yield sessionmaker(bind=concurrent_engine, expire_on_commit=False)
    finally:
        concurrent_engine.dispose()


def _reserve_one(sessions: sessionmaker[Session], barrier: Barrier, sku: str) -> str:
    with sessions() as session:
        # Nothing above this line touches the database, so the barrier releases twenty
        # threads that all want the same row at the same moment.
        barrier.wait(timeout=BARRIER_TIMEOUT_S)
        try:
            service.reserve(session, uuid.uuid4(), [ReservationItem(sku=sku, qty=1)])
            session.commit()
        except OutOfStockError:
            session.rollback()
            return "OUT_OF_STOCK"
    return "RESERVED"


def test_twenty_threads_cannot_oversell_ten_units(
    session: Session,
    make_product: Callable[..., Product],
    threaded_sessions: sessionmaker[Session],
) -> None:
    product = make_product("SKU-0001", stock=STOCK)
    barrier = Barrier(THREADS)

    with ThreadPoolExecutor(max_workers=THREADS) as pool:
        futures = [
            pool.submit(_reserve_one, threaded_sessions, barrier, product.sku)
            for _ in range(THREADS)
        ]
        outcomes = [future.result() for future in futures]

    assert outcomes.count("RESERVED") == STOCK
    assert outcomes.count("OUT_OF_STOCK") == THREADS - STOCK

    assert session.scalar(select(Product.stock).where(Product.id == product.id)) == 0

    rows = list(
        session.scalars(select(StockReservation).where(StockReservation.product_id == product.id))
    )
    assert len(rows) == STOCK
    assert {row.qty for row in rows} == {1}
    assert {row.status for row in rows} == {ReservationStatus.ACTIVE}
    # One reservation per order, not ten rows for whichever order happened to win.
    assert len({row.order_id for row in rows}) == STOCK
