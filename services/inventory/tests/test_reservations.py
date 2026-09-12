import uuid
from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Product, ReservationStatus, StockReservation

ORDER_ID = "11111111-1111-1111-1111-111111111111"
OTHER_ORDER_ID = "22222222-2222-2222-2222-222222222222"


def _reserve(client: TestClient, items: list[dict[str, Any]], order_id: str = ORDER_ID) -> Any:
    return client.post("/reservations", json={"order_id": order_id, "items": items})


def _stock(session: Session, product: Product) -> int:
    session.refresh(product)
    return product.stock


def _rows(session: Session, order_id: str = ORDER_ID) -> list[StockReservation]:
    statement = select(StockReservation).where(StockReservation.order_id == uuid.UUID(order_id))
    return list(session.scalars(statement))


def test_reserve_takes_stock_and_records_the_lines(
    client: TestClient, session: Session, make_product: Callable[..., Product]
) -> None:
    product = make_product("SKU-0001", stock=10)

    response = _reserve(client, [{"sku": "SKU-0001", "qty": 3}])

    assert response.status_code == 201
    body = response.json()
    assert body["order_id"] == ORDER_ID
    assert body["status"] == "ACTIVE"
    assert body["items"] == [{"sku": "SKU-0001", "qty": 3}]
    assert _stock(session, product) == 7
    assert [row.status for row in _rows(session)] == [ReservationStatus.ACTIVE]


def test_reserve_is_all_or_nothing(
    client: TestClient, session: Session, make_product: Callable[..., Product]
) -> None:
    first = make_product("SKU-0001", stock=10)
    second = make_product("SKU-0002", stock=1)

    response = _reserve(client, [{"sku": "SKU-0001", "qty": 2}, {"sku": "SKU-0002", "qty": 5}])

    assert response.status_code == 409
    assert _stock(session, first) == 10
    assert _stock(session, second) == 1
    assert _rows(session) == []


def test_reserving_exactly_the_last_unit_succeeds(
    client: TestClient, session: Session, make_product: Callable[..., Product]
) -> None:
    """INC-006: the conditional UPDATE guard was `stock > qty` instead of `stock >=
    qty`, so a request for exactly what remained matched zero rows and was rejected as
    out of stock. Every SKU eventually strands its last unit, forever - this is the
    boundary that bug lived in."""
    product = make_product("SKU-0007", stock=1)

    response = _reserve(client, [{"sku": "SKU-0007", "qty": 1}])

    assert response.status_code == 201
    assert _stock(session, product) == 0


def test_reserving_one_more_than_available_is_still_rejected(
    client: TestClient, session: Session, make_product: Callable[..., Product]
) -> None:
    product = make_product("SKU-0007", stock=1)

    response = _reserve(client, [{"sku": "SKU-0007", "qty": 2}])

    assert response.status_code == 409
    assert _stock(session, product) == 1


def test_out_of_stock_names_the_sku_and_the_numbers(
    client: TestClient, make_product: Callable[..., Product]
) -> None:
    make_product("SKU-0003", stock=2)

    response = _reserve(client, [{"sku": "SKU-0003", "qty": 5}])

    error = response.json()["error"]
    assert error["code"] == "OUT_OF_STOCK"
    assert error["message"] == "SKU-0003 has 2 units, 5 requested"
    assert error["request_id"]


def test_unknown_sku_is_rejected_before_any_stock_moves(
    client: TestClient, session: Session, make_product: Callable[..., Product]
) -> None:
    product = make_product("SKU-0001", stock=10)

    response = _reserve(client, [{"sku": "SKU-0001", "qty": 1}, {"sku": "SKU-9999", "qty": 1}])

    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "UNKNOWN_SKU"
    assert "SKU-9999" in error["message"]
    assert _stock(session, product) == 10


def test_duplicate_skus_in_one_request_are_rejected(
    client: TestClient, make_product: Callable[..., Product]
) -> None:
    make_product("SKU-0001", stock=10)

    response = _reserve(client, [{"sku": "SKU-0001", "qty": 1}, {"sku": "SKU-0001", "qty": 2}])

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_a_request_without_items_is_rejected(client: TestClient) -> None:
    response = _reserve(client, [])

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_the_same_order_id_never_takes_stock_twice(
    client: TestClient, session: Session, make_product: Callable[..., Product]
) -> None:
    product = make_product("SKU-0001", stock=10)

    first = _reserve(client, [{"sku": "SKU-0001", "qty": 3}])
    second = _reserve(client, [{"sku": "SKU-0001", "qty": 3}])

    assert first.json()["items"] == second.json()["items"]
    assert _stock(session, product) == 7
    assert len(_rows(session)) == 1


def test_two_orders_can_reserve_the_same_product(
    client: TestClient, session: Session, make_product: Callable[..., Product]
) -> None:
    product = make_product("SKU-0001", stock=10)

    _reserve(client, [{"sku": "SKU-0001", "qty": 3}])
    _reserve(client, [{"sku": "SKU-0001", "qty": 4}], order_id=OTHER_ORDER_ID)

    assert _stock(session, product) == 3


def test_commit_marks_the_rows_and_is_repeatable(
    client: TestClient, session: Session, make_product: Callable[..., Product]
) -> None:
    product = make_product("SKU-0001", stock=10)
    _reserve(client, [{"sku": "SKU-0001", "qty": 3}])

    first = client.post(f"/reservations/{ORDER_ID}/commit")
    second = client.post(f"/reservations/{ORDER_ID}/commit")

    assert first.status_code == 200
    assert first.json()["status"] == "COMMITTED"
    assert second.json() == first.json()
    assert _stock(session, product) == 7
    assert [row.status for row in _rows(session)] == [ReservationStatus.COMMITTED]


def test_commit_for_an_unknown_order(client: TestClient) -> None:
    response = client.post(f"/reservations/{ORDER_ID}/commit")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "UNKNOWN_RESERVATION"


def test_release_returns_the_stock_exactly_once(
    client: TestClient, session: Session, make_product: Callable[..., Product]
) -> None:
    product = make_product("SKU-0001", stock=10)
    _reserve(client, [{"sku": "SKU-0001", "qty": 3}])

    first = client.post(f"/reservations/{ORDER_ID}/release")
    assert first.status_code == 200
    assert first.json()["status"] == "RELEASED"
    assert _stock(session, product) == 10

    second = client.post(f"/reservations/{ORDER_ID}/release")
    assert second.status_code == 200
    assert _stock(session, product) == 10


def test_release_of_an_unknown_order_is_a_no_op(client: TestClient) -> None:
    response = client.post(f"/reservations/{ORDER_ID}/release")

    assert response.status_code == 200
    assert response.json() == {"order_id": ORDER_ID, "status": "RELEASED", "items": []}


def test_release_after_commit_returns_the_stock(
    client: TestClient, session: Session, make_product: Callable[..., Product]
) -> None:
    product = make_product("SKU-0001", stock=10)
    _reserve(client, [{"sku": "SKU-0001", "qty": 3}])
    client.post(f"/reservations/{ORDER_ID}/commit")

    client.post(f"/reservations/{ORDER_ID}/release")

    assert _stock(session, product) == 10


def test_commit_after_release_is_a_conflict(
    client: TestClient, make_product: Callable[..., Product]
) -> None:
    make_product("SKU-0001", stock=10)
    _reserve(client, [{"sku": "SKU-0001", "qty": 3}])
    client.post(f"/reservations/{ORDER_ID}/release")

    response = client.post(f"/reservations/{ORDER_ID}/commit")

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CONFLICT"


def test_a_released_order_can_reserve_again(
    client: TestClient, session: Session, make_product: Callable[..., Product]
) -> None:
    product = make_product("SKU-0001", stock=10)
    _reserve(client, [{"sku": "SKU-0001", "qty": 3}])
    client.post(f"/reservations/{ORDER_ID}/release")

    response = _reserve(client, [{"sku": "SKU-0001", "qty": 2}])

    assert response.json()["items"] == [{"sku": "SKU-0001", "qty": 2}]
    assert _stock(session, product) == 8


def test_multi_item_reservation_lists_every_line(
    client: TestClient, session: Session, make_product: Callable[..., Product]
) -> None:
    first = make_product("SKU-0002", stock=5)
    second = make_product("SKU-0001", stock=5)

    response = _reserve(client, [{"sku": "SKU-0002", "qty": 1}, {"sku": "SKU-0001", "qty": 2}])

    assert response.json()["items"] == [
        {"sku": "SKU-0001", "qty": 2},
        {"sku": "SKU-0002", "qty": 1},
    ]
    assert _stock(session, first) == 4
    assert _stock(session, second) == 3
