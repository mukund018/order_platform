from collections.abc import Callable
from datetime import timedelta

from fastapi.testclient import TestClient

from app.models import Order, OrderStatus, utcnow
from tests.conftest import Upstream


def test_health_does_not_touch_dependencies(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_checks_the_database_and_the_broker(client: TestClient) -> None:
    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "checks": {"database": "ok", "broker": "ok"}}


def test_metrics_count_orders_by_status(
    client: TestClient, make_order: Callable[..., Order]
) -> None:
    make_order(status=OrderStatus.CONFIRMED)
    make_order(status=OrderStatus.FAILED)

    body = client.get("/metrics").text

    assert 'orders_by_status{status="CONFIRMED"} 1.0' in body
    assert 'orders_by_status{status="FAILED"} 1.0' in body
    # Every status is exported, so a panel reads zero instead of going blank.
    assert 'orders_by_status{status="EXPIRED"} 0.0' in body


def test_metrics_count_orders_stuck_before_confirmation(
    client: TestClient, make_order: Callable[..., Order]
) -> None:
    make_order(status=OrderStatus.RESERVED, created_at=utcnow() - timedelta(minutes=30))
    make_order(status=OrderStatus.PENDING)

    body = client.get("/metrics").text

    assert "stale_pending_orders 1.0" in body


def test_metrics_count_transitions(client: TestClient, upstream: Upstream) -> None:
    client.post(
        "/orders",
        json={"customer_email": "buyer@example.com", "items": [{"sku": "SKU-0001", "qty": 1}]},
        headers={"Idempotency-Key": "metrics-key-1"},
    )

    body = client.get("/metrics").text

    assert 'orders_total{status="CONFIRMED"}' in body
    assert "http_request_duration_seconds_bucket" in body
