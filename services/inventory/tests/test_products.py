from collections.abc import Callable

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Product


def test_create_product_returns_the_stored_row(client: TestClient) -> None:
    response = client.post(
        "/products",
        json={"sku": "SKU-0001", "name": "Kettle", "price_paise": 249900, "stock": 4},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["sku"] == "SKU-0001"
    assert body["price_paise"] == 249900
    assert body["stock"] == 4
    assert body["id"]
    assert response.headers["X-Request-ID"]


def test_create_rejects_a_duplicate_sku(
    client: TestClient, make_product: Callable[..., Product]
) -> None:
    make_product("SKU-0001")

    response = client.post(
        "/products",
        json={"sku": "SKU-0001", "name": "Kettle", "price_paise": 100, "stock": 1},
    )

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "CONFLICT"
    assert "SKU-0001" in error["message"]
    assert error["request_id"]


def test_create_rejects_a_zero_price(client: TestClient) -> None:
    response = client.post(
        "/products",
        json={"sku": "SKU-0002", "name": "Free lunch", "price_paise": 0, "stock": 1},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_list_is_ordered_by_sku(client: TestClient, make_product: Callable[..., Product]) -> None:
    make_product("SKU-0003")
    make_product("SKU-0001")
    make_product("SKU-0002")

    response = client.get("/products")

    assert response.status_code == 200
    assert [row["sku"] for row in response.json()] == ["SKU-0001", "SKU-0002", "SKU-0003"]


def test_get_one_product(client: TestClient, make_product: Callable[..., Product]) -> None:
    make_product("SKU-0007", name="Toaster", price_paise=550000, stock=3)

    body = client.get("/products/SKU-0007").json()

    assert body["name"] == "Toaster"
    assert body["price_paise"] == 550000
    assert body["stock"] == 3


def test_get_unknown_sku_uses_the_standard_error_body(client: TestClient) -> None:
    response = client.get("/products/SKU-9999")

    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "NOT_FOUND"
    assert "SKU-9999" in error["message"]
    assert error["request_id"] == response.headers["X-Request-ID"]


def test_adjust_stock_adds_units(
    client: TestClient, session: Session, make_product: Callable[..., Product]
) -> None:
    product = make_product("SKU-0001", stock=10)

    response = client.patch("/products/SKU-0001/stock", json={"delta": 5})

    assert response.status_code == 200
    assert response.json()["stock"] == 15
    session.refresh(product)
    assert product.stock == 15


def test_adjust_stock_removes_units(
    client: TestClient, make_product: Callable[..., Product]
) -> None:
    make_product("SKU-0001", stock=10)

    response = client.patch("/products/SKU-0001/stock", json={"delta": -4})

    assert response.json()["stock"] == 6


def test_adjust_stock_cannot_go_negative(
    client: TestClient, session: Session, make_product: Callable[..., Product]
) -> None:
    product = make_product("SKU-0001", stock=3)

    response = client.patch("/products/SKU-0001/stock", json={"delta": -5})

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "OUT_OF_STOCK"
    assert error["message"] == "SKU-0001 has 3 units, cannot remove 5"
    session.refresh(product)
    assert product.stock == 3


def test_adjust_stock_on_unknown_sku(client: TestClient) -> None:
    response = client.patch("/products/SKU-9999/stock", json={"delta": 1})

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
