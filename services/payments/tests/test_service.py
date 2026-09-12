import uuid

import pytest
from sqlalchemy.orm import Session

from app import service
from app.models import Payment, PaymentStatus
from common.errors import ConflictError, NotFoundError
from tests.conftest import StubGateway, approved, declined


def test_get_payment_raises_not_found(session: Session, order_id: uuid.UUID) -> None:
    with pytest.raises(NotFoundError) as caught:
        service.get_payment(session, order_id)

    assert caught.value.code == "NOT_FOUND"
    assert caught.value.status_code == 404


def test_losing_the_insert_race_does_not_charge(
    session: Session, gateway: StubGateway, order_id: uuid.UUID, monkeypatch: pytest.MonkeyPatch
) -> None:
    session.add(Payment(order_id=order_id, amount_paise=700, status=PaymentStatus.PENDING))
    session.commit()

    # Pretend the row appeared between our lookup and our insert: the first lookup sees
    # nothing, so charge() tries to insert and collides with the unique index.
    real_find = service.find_payment
    lookups = {"count": 0}

    def find_blind_once(db: Session, oid: uuid.UUID) -> Payment | None:
        lookups["count"] += 1
        return None if lookups["count"] == 1 else real_find(db, oid)

    monkeypatch.setattr(service, "find_payment", find_blind_once)

    with pytest.raises(ConflictError) as caught:
        service.charge(session, order_id, 700, gateway)

    assert caught.value.code == "PAYMENT_IN_PROGRESS"
    assert gateway.calls == []
    assert session.query(Payment).filter_by(order_id=order_id).count() == 1


def test_retry_clears_the_previous_failure_reason(session: Session, order_id: uuid.UUID) -> None:
    gateway = StubGateway(declined("expired_card"), approved("PAY-ffff00001111"))

    first = service.charge(session, order_id, 700, gateway)
    assert first.status is PaymentStatus.FAILED
    assert first.failure_reason == "expired_card"

    second = service.charge(session, order_id, 700, gateway)

    assert second.id == first.id
    assert second.status is PaymentStatus.SUCCEEDED
    assert second.failure_reason is None
    assert second.provider_ref == "PAY-ffff00001111"
    assert second.attempts == 2


def test_mismatched_amount_on_a_failed_payment_is_still_refused(
    session: Session, order_id: uuid.UUID
) -> None:
    gateway = StubGateway(declined())
    service.charge(session, order_id, 700, gateway)

    with pytest.raises(ConflictError) as caught:
        service.charge(session, order_id, 800, gateway)

    assert caught.value.code == "PAYMENT_AMOUNT_MISMATCH"
    assert len(gateway.calls) == 1
