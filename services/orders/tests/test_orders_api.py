import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from kombu.exceptions import OperationalError as BrokerError
from prometheus_client import REGISTRY
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import service, tasks
from app.models import Order, OrderStatus
from tests.conftest import Upstream, error_body

KEY = "idem-key-0001"
ITEMS = [{"sku": "SKU-0001", "qty": 2}]
TOTAL_PAISE = 2 * 19900


def place(
    client: TestClient,
    *,
    key: str | None = KEY,
    items: list[dict[str, Any]] | None = None,
    email: str = "buyer@example.com",
    request_id: str | None = None,
) -> httpx.Response:
    headers = {}
    if key is not None:
        headers["Idempotency-Key"] = key
    if request_id is not None:
        headers["X-Request-ID"] = request_id
    return client.post(
        "/orders",
        json={"customer_email": email, "items": ITEMS if items is None else items},
        headers=headers,
    )


def statuses(body: dict[str, Any]) -> list[str]:
    return [event["to_status"] for event in body["events"]]


def count_orders(session: Session) -> int:
    return session.scalar(select(func.count(Order.id))) or 0


def orders_counted(status: str) -> float:
    return REGISTRY.get_sample_value("orders_total", {"status": status}) or 0.0


def test_a_paid_order_is_confirmed(
    client: TestClient, session: Session, upstream: Upstream
) -> None:
    response = place(client)

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "CONFIRMED"
    assert body["total_paise"] == TOTAL_PAISE
    assert body["failure_reason"] is None
    assert body["items"][0]["sku"] == "SKU-0001"
    assert body["items"][0]["qty"] == 2
    assert body["items"][0]["unit_price_paise"] == 19900
    assert statuses(body) == ["PENDING", "RESERVED", "CONFIRMED"]
    assert upstream.reserve.call_count == 1
    assert upstream.commit.call_count == 1
    assert upstream.release.call_count == 0
    assert count_orders(session) == 1


def test_the_amount_charged_is_the_order_total(client: TestClient, upstream: Upstream) -> None:
    place(client, items=[{"sku": "SKU-0001", "qty": 1}, {"sku": "SKU-0002", "qty": 3}])

    charged = json.loads(upstream.charge.calls.last.request.content)
    assert charged["amount_paise"] == 19900 + 3 * 4500


def test_out_of_stock_fails_the_order_without_charging_anyone(
    client: TestClient, upstream: Upstream
) -> None:
    upstream.out_of_stock("SKU-0001", available=1, requested=2)

    response = place(client)

    # The request succeeded; the order did not. A refused order is not an HTTP error.
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "FAILED"
    assert body["failure_reason"] == "SKU-0001 has 1 units, 2 requested"
    assert statuses(body) == ["PENDING", "FAILED"]
    assert upstream.charge.call_count == 0
    # Nothing was reserved, so there is nothing to hand back.
    assert upstream.release.call_count == 0


def test_a_declined_card_fails_the_order_and_gives_the_stock_back(
    client: TestClient, upstream: Upstream
) -> None:
    upstream.decline("insufficient_funds")

    body = place(client).json()

    assert body["status"] == "FAILED"
    assert body["failure_reason"] == "insufficient_funds"
    assert statuses(body) == ["PENDING", "RESERVED", "FAILED"]
    assert upstream.release.call_count == 1
    assert upstream.commit.call_count == 0


def test_a_payment_timeout_fails_the_order_and_gives_the_stock_back(
    client: TestClient, upstream: Upstream
) -> None:
    upstream.payment_times_out()

    body = place(client).json()

    assert body["status"] == "FAILED"
    assert "did not answer" in body["failure_reason"]
    assert upstream.release.call_count == 1


def test_a_release_that_fails_does_not_hide_the_payment_failure(
    client: TestClient, upstream: Upstream
) -> None:
    upstream.decline()
    upstream.release.mock(
        return_value=httpx.Response(500, json=error_body("INTERNAL_ERROR", "inventory is unwell"))
    )

    response = place(client)

    assert response.status_code == 201
    assert response.json()["status"] == "FAILED"
    assert upstream.release.call_count == 1


def test_a_reservation_that_never_answers_leaves_the_order_pending(
    client: TestClient, session: Session, upstream: Upstream
) -> None:
    upstream.reserve.mock(side_effect=httpx.ReadTimeout("timed out"))

    response = place(client)

    # We do not know whether inventory took the stock, so the order is left for
    # expire_stale_orders to release rather than guessed at here.
    assert response.status_code == 504
    assert response.json()["error"]["code"] == "UPSTREAM_TIMEOUT"
    order = session.scalars(select(Order)).one()
    assert order.status is OrderStatus.PENDING


def test_a_sku_that_disappears_between_pricing_and_reserving_fails_the_order(
    client: TestClient, upstream: Upstream
) -> None:
    upstream.unknown_sku_on_reserve("SKU-0001")

    body = place(client).json()

    # UNKNOWN_SKU from the reservation is a refusal like OUT_OF_STOCK, not a fault: the
    # order ends FAILED rather than leaving the caller a 502 and a PENDING order.
    assert body["status"] == "FAILED"
    assert body["failure_reason"] == "unknown sku: SKU-0001"
    assert statuses(body) == ["PENDING", "FAILED"]
    assert upstream.release.call_count == 0
    assert upstream.charge.call_count == 0


def test_an_unknown_sku_is_rejected_whichever_code_inventory_uses(
    client: TestClient, session: Session, upstream: Upstream
) -> None:
    upstream.unknown_product("SKU-9999", code="UNKNOWN_SKU")

    response = place(client, items=[{"sku": "SKU-9999", "qty": 1}])

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "UNKNOWN_SKU"
    assert count_orders(session) == 0


def test_the_same_idempotency_key_places_exactly_one_order(
    client: TestClient, session: Session, upstream: Upstream
) -> None:
    first = place(client)
    second = place(client)

    assert second.json() == first.json()
    assert count_orders(session) == 1
    # The replay must not touch inventory or payments a second time.
    assert upstream.reserve.call_count == 1
    assert upstream.charge.call_count == 1
    assert upstream.commit.call_count == 1


def test_a_key_replayed_while_its_order_is_unfinished_is_refused(
    client: TestClient, session: Session, upstream: Upstream
) -> None:
    upstream.reserve.mock(side_effect=httpx.ReadTimeout("timed out"))
    assert place(client).status_code == 504

    response = place(client)

    # The retry is the right thing for the client to do, but the order is not finished
    # and may still be moving, so it is told to look rather than handed a 201 that says
    # PENDING and means nothing happened.
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ORDER_IN_PROGRESS"
    assert count_orders(session) == 1
    assert upstream.charge.call_count == 0


def test_a_key_reused_for_a_different_order_is_refused(
    client: TestClient, session: Session, upstream: Upstream
) -> None:
    place(client, email="first@example.com")

    response = place(client, email="second@example.com")

    # Returning the first customer's order here would hand their address and items to
    # somebody else, with a 201 that reads as a successful placement.
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert count_orders(session) == 1


def test_a_key_replayed_with_the_same_lines_in_another_order_is_accepted(
    client: TestClient, upstream: Upstream
) -> None:
    first = place(client, items=[{"sku": "SKU-0001", "qty": 1}, {"sku": "SKU-0002", "qty": 2}])

    second = place(client, items=[{"sku": "SKU-0002", "qty": 2}, {"sku": "SKU-0001", "qty": 1}])

    assert second.status_code == 201
    assert second.json()["id"] == first.json()["id"]


def test_two_requests_racing_on_one_key_settle_on_the_unique_index(
    client: TestClient,
    session: Session,
    upstream: Upstream,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    place(client)
    real_lookup = service.find_by_idempotency_key
    lookups = {"count": 0}

    def blind_once(db: Session, key: str) -> Order | None:
        lookups["count"] += 1
        return None if lookups["count"] == 1 else real_lookup(db, key)

    # Pretend the row appeared between our lookup and our insert: the second request
    # sees nothing, tries to insert, and collides with the unique index.
    monkeypatch.setattr(service, "find_by_idempotency_key", blind_once)
    response = place(client)

    assert response.status_code == 201
    assert response.json()["status"] == "CONFIRMED"
    assert count_orders(session) == 1
    assert upstream.reserve.call_count == 1


def test_a_commit_that_fails_after_payment_still_confirms_the_order(
    client: TestClient, upstream: Upstream
) -> None:
    upstream.commit.mock(
        return_value=httpx.Response(500, json=error_body("INTERNAL_ERROR", "inventory is unwell"))
    )

    body = place(client).json()

    # The card was charged. Failing the order would lose the money and releasing the
    # stock would be wrong, so the order stands and the error log carries the problem.
    assert body["status"] == "CONFIRMED"
    assert upstream.release.call_count == 0


def test_a_broker_that_is_down_does_not_fail_a_paid_order(
    client: TestClient, upstream: Upstream, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*_args: object, **_kwargs: object) -> None:
        raise BrokerError("cannot connect to redis")

    monkeypatch.setattr(tasks.send_confirmation, "delay", refuse)

    body = place(client).json()

    assert body["status"] == "CONFIRMED"


def test_inventory_failing_while_pricing_is_not_reported_as_an_unknown_sku(
    client: TestClient, session: Session, upstream: Upstream
) -> None:
    upstream.product_unavailable("SKU-0001")

    response = place(client)

    assert response.status_code == 502
    body = response.json()
    assert body["error"]["code"] == "UPSTREAM_ERROR"
    assert body["error"]["details"]["upstream"] == "inventory"
    assert count_orders(session) == 0


def test_a_different_key_places_a_second_order(
    client: TestClient, session: Session, upstream: Upstream
) -> None:
    place(client, key="idem-key-a")
    place(client, key="idem-key-b")

    assert count_orders(session) == 2


def test_a_missing_idempotency_key_is_refused(
    client: TestClient, session: Session, upstream: Upstream
) -> None:
    response = place(client, key=None)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"
    assert count_orders(session) == 0
    assert upstream.reserve.call_count == 0


def test_a_blank_idempotency_key_is_refused(client: TestClient) -> None:
    response = place(client, key="   ")

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "IDEMPOTENCY_KEY_REQUIRED"


def test_an_over_long_idempotency_key_is_refused(client: TestClient) -> None:
    response = place(client, key="k" * 65)

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "IDEMPOTENCY_KEY_TOO_LONG"


def test_an_unknown_sku_is_rejected_before_an_order_is_written(
    client: TestClient, session: Session, upstream: Upstream
) -> None:
    upstream.unknown_product("SKU-9999")

    response = place(client, items=[{"sku": "SKU-9999", "qty": 1}])

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "UNKNOWN_SKU"
    assert count_orders(session) == 0


def test_the_same_sku_twice_in_one_order_is_rejected(client: TestClient) -> None:
    response = place(client, items=[{"sku": "SKU-0001", "qty": 1}, {"sku": "SKU-0001", "qty": 2}])

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_an_order_with_no_items_is_rejected(client: TestClient) -> None:
    assert place(client, items=[]).status_code == 422


def test_an_address_that_is_not_an_email_is_rejected(client: TestClient) -> None:
    assert place(client, email="not-an-email").status_code == 422


def test_the_request_id_reaches_both_upstreams_and_the_audit_trail(
    client: TestClient, upstream: Upstream
) -> None:
    body = place(client, request_id="trace-me-0001").json()

    assert upstream.reserve.calls.last.request.headers["X-Request-ID"] == "trace-me-0001"
    assert upstream.charge.calls.last.request.headers["X-Request-ID"] == "trace-me-0001"
    assert {event["request_id"] for event in body["events"]} == {"trace-me-0001"}


def test_reading_one_order_returns_its_items_and_history(
    client: TestClient, upstream: Upstream
) -> None:
    order_id = place(client).json()["id"]

    response = client.get(f"/orders/{order_id}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == order_id
    assert body["items"][0]["sku"] == "SKU-0001"
    assert statuses(body) == ["PENDING", "RESERVED", "CONFIRMED"]


def test_reading_an_order_that_does_not_exist(client: TestClient) -> None:
    response = client.get(f"/orders/{uuid.uuid4()}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_listing_filters_by_status(client: TestClient, upstream: Upstream) -> None:
    place(client, key="key-confirmed")
    upstream.decline()
    place(client, key="key-failed")

    failed = client.get("/orders", params={"status": "FAILED"}).json()
    confirmed = client.get("/orders", params={"status": "CONFIRMED"}).json()

    assert [row["status"] for row in failed] == ["FAILED"]
    assert [row["status"] for row in confirmed] == ["CONFIRMED"]


def test_listing_pages_newest_first(client: TestClient, make_order: Callable[..., Order]) -> None:
    older = make_order(created_at=datetime(2025, 11, 10, 9, 0, tzinfo=UTC))
    newer = make_order(created_at=datetime(2025, 11, 11, 9, 0, tzinfo=UTC))

    page = client.get("/orders", params={"limit": 1}).json()
    next_page = client.get("/orders", params={"limit": 1, "offset": 1}).json()

    assert [row["id"] for row in page] == [str(newer.id)]
    assert [row["id"] for row in next_page] == [str(older.id)]


def test_paging_never_repeats_an_order_when_timestamps_tie(
    client: TestClient, make_order: Callable[..., Order]
) -> None:
    # created_at is only as fine-grained as the clock, and traffic at 10 rps produces
    # orders that share one. Without a unique tie-break the pages overlap.
    moment = datetime(2025, 11, 11, 9, 0, tzinfo=UTC)
    for _ in range(3):
        make_order(created_at=moment)

    first = client.get("/orders", params={"limit": 2}).json()
    second = client.get("/orders", params={"limit": 2, "offset": 2}).json()

    ids = [row["id"] for row in first + second]
    assert len(ids) == 3
    assert len(set(ids)) == 3


def test_a_limit_above_the_maximum_is_rejected(client: TestClient) -> None:
    assert client.get("/orders", params={"limit": 500}).status_code == 422


def test_cancelling_a_confirmed_order_returns_the_stock(
    client: TestClient, upstream: Upstream
) -> None:
    order_id = place(client).json()["id"]

    response = client.post(f"/orders/{order_id}/cancel")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "CANCELLED"
    assert statuses(body) == ["PENDING", "RESERVED", "CONFIRMED", "CANCELLED"]
    assert upstream.release.call_count == 1


def test_a_cancel_whose_release_fails_leaves_the_order_confirmed(
    client: TestClient, upstream: Upstream
) -> None:
    order_id = place(client).json()["id"]
    upstream.release.mock(
        return_value=httpx.Response(500, json=error_body("INTERNAL_ERROR", "inventory is unwell"))
    )
    counted = orders_counted("CANCELLED")

    response = client.post(f"/orders/{order_id}/cancel")

    # The one release in the service that is allowed to fail the request: the customer
    # can ask again, and reporting a cancellation whose stock never came back would be
    # worse. Nothing may be left behind by the attempt, counter included.
    assert response.status_code == 502
    body = client.get(f"/orders/{order_id}").json()
    assert body["status"] == "CONFIRMED"
    assert statuses(body) == ["PENDING", "RESERVED", "CONFIRMED"]
    assert orders_counted("CANCELLED") == counted


def test_cancelling_a_pending_order_is_refused(
    client: TestClient, session: Session, upstream: Upstream
) -> None:
    upstream.reserve.mock(side_effect=httpx.ReadTimeout("timed out"))
    place(client)
    order = session.scalars(select(Order)).one()

    response = client.post(f"/orders/{order.id}/cancel")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ILLEGAL_TRANSITION"
    assert upstream.release.call_count == 0
    session.refresh(order)
    assert order.status is OrderStatus.PENDING


def test_cancelling_twice_is_refused(client: TestClient, upstream: Upstream) -> None:
    order_id = place(client).json()["id"]
    client.post(f"/orders/{order_id}/cancel")

    response = client.post(f"/orders/{order_id}/cancel")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ILLEGAL_TRANSITION"
    assert upstream.release.call_count == 1


def test_cancelling_an_order_that_does_not_exist(client: TestClient) -> None:
    assert client.post(f"/orders/{uuid.uuid4()}/cancel").status_code == 404
