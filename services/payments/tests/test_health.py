import uuid

from fastapi.testclient import TestClient


def test_health_does_not_touch_the_database(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_checks_the_database(client: TestClient) -> None:
    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json()["checks"]["database"] == "ok"


def test_metrics_expose_payment_counters(client: TestClient) -> None:
    client.post("/payments", json={"order_id": str(uuid.uuid4()), "amount_paise": 100})

    body = client.get("/metrics").text

    assert 'payment_attempts_total{result="succeeded"}' in body
    assert "payment_gateway_duration_seconds_bucket" in body
