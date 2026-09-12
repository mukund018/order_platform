import uuid

import httpx
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Payment, PaymentStatus
from tests.conftest import StubGateway, approved, declined


def post_payment(
    client: TestClient, order_id: uuid.UUID, amount_paise: int = 45_000
) -> httpx.Response:
    return client.post("/payments", json={"order_id": str(order_id), "amount_paise": amount_paise})


def test_approved_payment_is_recorded(
    client: TestClient, gateway: StubGateway, session: Session, order_id: uuid.UUID
) -> None:
    response = post_payment(client, order_id)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "SUCCEEDED"
    assert body["provider_ref"] == "PAY-0123456789ab"
    assert body["failure_reason"] is None
    assert body["attempts"] == 1
    assert body["amount_paise"] == 45_000
    assert gateway.calls == [(order_id, 45_000)]

    stored = session.get(Payment, uuid.UUID(body["id"]))
    assert stored is not None
    assert stored.status is PaymentStatus.SUCCEEDED


def test_decline_is_a_successful_response_with_failed_status(
    client: TestClient, gateway: StubGateway, order_id: uuid.UUID
) -> None:
    gateway.queued = [declined("card_declined")]

    response = post_payment(client, order_id)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "FAILED"
    assert body["failure_reason"] == "card_declined"
    assert body["provider_ref"] is None
    assert body["attempts"] == 1


def test_retry_after_a_decline_increments_attempts(
    client: TestClient, gateway: StubGateway, order_id: uuid.UUID
) -> None:
    gateway.queued = [declined(), approved("PAY-aaaabbbbcccc")]

    first = post_payment(client, order_id).json()
    second = post_payment(client, order_id).json()

    assert first["status"] == "FAILED"
    assert first["attempts"] == 1
    assert second["status"] == "SUCCEEDED"
    assert second["attempts"] == 2
    assert second["failure_reason"] is None
    assert second["id"] == first["id"]
    assert len(gateway.calls) == 2


def test_repeat_after_success_never_charges_twice(
    client: TestClient, gateway: StubGateway, order_id: uuid.UUID
) -> None:
    first = post_payment(client, order_id).json()
    second = post_payment(client, order_id)

    assert second.status_code == 200
    body = second.json()
    assert body == first
    assert len(gateway.calls) == 1


def test_amount_mismatch_is_refused(
    client: TestClient, gateway: StubGateway, order_id: uuid.UUID
) -> None:
    post_payment(client, order_id, 45_000)

    response = post_payment(client, order_id, 45_001)

    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "PAYMENT_AMOUNT_MISMATCH"
    assert "45000" in error["message"]
    assert error["request_id"]
    assert len(gateway.calls) == 1


def test_attempt_in_flight_is_refused(
    client: TestClient, gateway: StubGateway, session: Session, order_id: uuid.UUID
) -> None:
    session.add(Payment(order_id=order_id, amount_paise=45_000, status=PaymentStatus.PENDING))
    session.commit()

    response = post_payment(client, order_id)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PAYMENT_IN_PROGRESS"
    assert gateway.calls == []


def test_get_returns_the_payment(client: TestClient, order_id: uuid.UUID) -> None:
    created = post_payment(client, order_id).json()

    response = client.get(f"/payments/{order_id}")

    assert response.status_code == 200
    assert response.json() == created


def test_get_unknown_order_is_404(client: TestClient) -> None:
    response = client.get(f"/payments/{uuid.uuid4()}")

    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "NOT_FOUND"
    assert error["request_id"]


def test_amount_must_be_positive(client: TestClient, gateway: StubGateway) -> None:
    response = post_payment(client, uuid.uuid4(), 0)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"
    assert gateway.calls == []


def test_order_id_must_be_a_uuid(client: TestClient) -> None:
    response = client.post("/payments", json={"order_id": "not-a-uuid", "amount_paise": 100})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_request_id_is_echoed_back(client: TestClient, order_id: uuid.UUID) -> None:
    response = client.post(
        "/payments",
        json={"order_id": str(order_id), "amount_paise": 500},
        headers={"X-Request-ID": "trace-me"},
    )

    assert response.headers["X-Request-ID"] == "trace-me"
