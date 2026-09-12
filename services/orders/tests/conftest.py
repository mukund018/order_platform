# ruff: noqa: E402 - app/config.py reads the environment at import time, and importing
# anything under app/ imports it, so the variables have to be set before that happens.
import json
import os
import tempfile
import uuid
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx

INVENTORY_URL = "http://inventory.test"
PAYMENTS_URL = "http://payments.test"

DEFAULT_CATALOGUE = {"SKU-0001": 19900, "SKU-0002": 4500}

_TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
if not _TEST_DATABASE_URL:
    _scratch = Path(tempfile.mkdtemp(prefix="orders-tests-")) / "orders.db"
    _TEST_DATABASE_URL = f"sqlite+pysqlite:///{_scratch.as_posix()}"

os.environ["ORDERS_DATABASE_URL"] = _TEST_DATABASE_URL
os.environ["INVENTORY_URL"] = INVENTORY_URL
os.environ["PAYMENTS_URL"] = PAYMENTS_URL
os.environ["CELERY_BROKER_URL"] = "memory://"
# Pinned, not defaulted: several tests assert the 15 minute wording and the IST windows,
# so an exported value or a stray .env must not decide what they are checking against.
os.environ["ORDER_EXPIRY_MINUTES"] = "15"
os.environ["BUSINESS_TIMEZONE"] = "Asia/Kolkata"
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.pop("LOG_DIR", None)

from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.db import SessionLocal, engine, get_session
from app.main import app
from app.models import Base, Order, OrderItem, OrderStatus
from common.testing import require_test_database


def error_body(code: str, message: str) -> dict[str, Any]:
    """The standard envelope every service in the platform returns for a non-2xx."""
    return {"error": {"code": code, "message": message, "request_id": "test-request-id"}}


class Upstream:
    """inventory-service and payments-service, faked at the HTTP boundary.

    Mocking here rather than swapping the client objects out means every test also
    exercises the real client code: the timeouts, the error translation, and the
    request id going out on the wire.
    """

    def __init__(self, router: respx.Router) -> None:
        self.router = router
        self._product_routes: dict[str, respx.Route] = {}
        for sku, price_paise in DEFAULT_CATALOGUE.items():
            self.product(sku, price_paise=price_paise)

        self.reserve = router.post(f"{INVENTORY_URL}/reservations").mock(
            side_effect=_reservation("ACTIVE", 201)
        )
        self.commit = router.post(url__regex=rf"{INVENTORY_URL}/reservations/[^/]+/commit").mock(
            side_effect=_reservation("COMMITTED")
        )
        self.release = router.post(url__regex=rf"{INVENTORY_URL}/reservations/[^/]+/release").mock(
            side_effect=_reservation("RELEASED")
        )
        self.charge = router.post(f"{PAYMENTS_URL}/payments").mock(side_effect=_payment(True))

    def product(self, sku: str, *, price_paise: int = 19900, stock: int = 10) -> dict[str, Any]:
        product = {
            "id": str(uuid.uuid4()),
            "sku": sku,
            "name": f"Product {sku}",
            "price_paise": price_paise,
            "stock": stock,
        }
        self._product_route(sku).mock(return_value=httpx.Response(200, json=product))
        return product

    def unknown_product(self, sku: str, *, code: str = "NOT_FOUND") -> None:
        """Pricing answers NOT_FOUND; the reservation endpoint uses UNKNOWN_SKU for the
        same thing, and orders-service has to treat both alike."""
        self._product_route(sku).mock(
            return_value=httpx.Response(404, json=error_body(code, f"no product {sku}"))
        )

    def product_unavailable(self, sku: str) -> None:
        self._product_route(sku).mock(
            return_value=httpx.Response(
                500, json=error_body("INTERNAL_ERROR", "inventory is unwell")
            )
        )

    def _product_route(self, sku: str) -> respx.Route:
        # One route per sku, re-mocked rather than replaced: respx answers with the first
        # route that matches, so a second one for the same url would never be reached.
        if sku not in self._product_routes:
            self._product_routes[sku] = self.router.get(f"{INVENTORY_URL}/products/{sku}")
        return self._product_routes[sku]

    def out_of_stock(self, sku: str, *, available: int = 0, requested: int = 1) -> None:
        self.reserve.mock(
            return_value=httpx.Response(
                409,
                json=error_body(
                    "OUT_OF_STOCK", f"{sku} has {available} units, {requested} requested"
                ),
            )
        )

    def unknown_sku_on_reserve(self, sku: str) -> None:
        """The sku existed when the order was priced and was gone by the time we reserved."""
        self.reserve.mock(
            return_value=httpx.Response(404, json=error_body("UNKNOWN_SKU", f"unknown sku: {sku}"))
        )

    def decline(self, reason: str = "insufficient_funds") -> None:
        self.charge.mock(side_effect=_payment(False, failure_reason=reason))

    def payment_times_out(self) -> None:
        self.charge.mock(side_effect=httpx.ReadTimeout("timed out"))


def _reservation(status: str, status_code: int = 200) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        order_id = body.get("order_id") or request.url.path.split("/")[2]
        return httpx.Response(
            status_code,
            json={"order_id": order_id, "status": status, "items": body.get("items", [])},
        )

    return handler


def _payment(
    approved: bool, failure_reason: str | None = None
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "id": str(uuid.uuid4()),
                "order_id": body["order_id"],
                "amount_paise": body["amount_paise"],
                "status": "SUCCEEDED" if approved else "FAILED",
                "provider_ref": "PAY-0123456789ab" if approved else None,
                "failure_reason": failure_reason,
                "attempts": 1,
                "created_at": "2025-11-12T10:00:00+00:00",
            },
        )

    return handler


@pytest.fixture(scope="session", autouse=True)
def _schema() -> Iterator[None]:
    require_test_database(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(delete(table))


@pytest.fixture
def session() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session


@pytest.fixture
def upstream() -> Iterator[Upstream]:
    with respx.mock(assert_all_called=False) as router:
        yield Upstream(router)


@pytest.fixture
def client(session: Session) -> Iterator[TestClient]:
    """The app runs on the test's own session, so assertions see what it wrote."""

    def use_test_session() -> Iterator[Session]:
        # Same contract as the real get_session, rollback included - otherwise a test
        # would see partial writes that a live request would have thrown away.
        try:
            yield session
        except Exception:
            session.rollback()
            raise

    app.dependency_overrides[get_session] = use_test_session
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def make_order(session: Session) -> Callable[..., Order]:
    def factory(
        *,
        status: OrderStatus = OrderStatus.PENDING,
        sku: str = "SKU-0001",
        qty: int = 1,
        unit_price_paise: int = 19900,
        created_at: datetime | None = None,
        customer_email: str = "buyer@example.com",
    ) -> Order:
        order = Order(
            customer_email=customer_email,
            idempotency_key=uuid.uuid4().hex,
            status=status,
            total_paise=qty * unit_price_paise,
            items=[
                OrderItem(
                    product_id=uuid.uuid4(),
                    sku=sku,
                    qty=qty,
                    unit_price_paise=unit_price_paise,
                )
            ],
        )
        if created_at is not None:
            order.created_at = created_at
        session.add(order)
        session.commit()
        return order

    return factory
