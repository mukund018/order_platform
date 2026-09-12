from collections.abc import Callable

from fastapi.testclient import TestClient
from tests.test_cache import BrokenRedis

from app import cache as cache_module
from app.cache import ProductCache
from app.models import Product


def test_health_does_not_touch_dependencies(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_reports_every_dependency(client: TestClient) -> None:
    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": {"database": "ok", "redis": "ok"}}


def test_ready_fails_when_redis_is_down(client: TestClient) -> None:
    cache_module.set_cache(ProductCache(BrokenRedis(), ttl=45))

    response = client.get("/ready")

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "DEPENDENCY_UNAVAILABLE"
    assert error["details"]["checks"]["redis"].startswith("error")


def test_metrics_exposes_stock_for_tracked_skus(
    client: TestClient, make_product: Callable[..., Product]
) -> None:
    make_product("SKU-0001", stock=7)
    make_product("SKU-0009", stock=3)

    body = client.get("/metrics").text

    assert 'product_stock{sku="SKU-0001"} 7.0' in body
    assert "SKU-0009" not in body


def test_metrics_counts_reservation_failures(
    client: TestClient, make_product: Callable[..., Product]
) -> None:
    make_product("SKU-0001", stock=1)
    client.post(
        "/reservations",
        json={
            "order_id": "33333333-3333-3333-3333-333333333333",
            "items": [{"sku": "SKU-0001", "qty": 4}],
        },
    )

    body = client.get("/metrics").text

    assert 'reservation_failures_total{reason="out_of_stock"}' in body
