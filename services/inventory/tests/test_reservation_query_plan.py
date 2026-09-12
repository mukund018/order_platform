"""Regression test for INC-003: stock_reservations.order_id must stay indexed.

The model and the migration have always declared this index correctly - INC-003 was
a live database missing it at runtime, not a code bug (`DROP INDEX` run directly
against the database, outside any migration). No application code change could have
caught that, so this test checks the thing that actually broke: the query plan
itself. If the index is ever missing again, for any reason, this fails loudly
instead of quietly costing a full table scan on every reserve/commit/release.
"""

import os
from collections.abc import Callable

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import Product, ReservationStatus, StockReservation

pytestmark = [
    pytest.mark.db,
    pytest.mark.skipif(
        not os.environ.get("TEST_DATABASE_URL"),
        reason="query plans are a real-postgres thing: set TEST_DATABASE_URL",
    ),
]


def test_reservation_lookup_by_order_id_does_not_seq_scan(
    session: Session, make_product: Callable[..., Product]
) -> None:
    product = make_product("SKU-0001", stock=100)
    order_id = "5d1bec60-a85b-4bc7-aafa-99cf144f14c9"
    session.add(
        StockReservation(
            order_id=order_id,
            product_id=product.id,
            qty=1,
            status=ReservationStatus.ACTIVE,
        )
    )
    session.commit()

    plan = (
        session.execute(
            text(
                "EXPLAIN (FORMAT TEXT) SELECT id FROM stock_reservations "
                "WHERE order_id = :order_id AND status IN ('ACTIVE', 'COMMITTED')"
            ),
            {"order_id": order_id},
        )
        .scalars()
        .all()
    )
    plan_text = "\n".join(plan)

    assert "Seq Scan on stock_reservations" not in plan_text, (
        "stock_reservations.order_id lookup is doing a full table scan - the index "
        "is missing. This is INC-003 happening again: every reserve/commit/release "
        "call scans the whole table, and it only gets worse as the table grows."
    )
