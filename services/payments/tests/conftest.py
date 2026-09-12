import os
import tempfile
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

# app.config demands PAYMENTS_DATABASE_URL, and app.db builds its engine at import
# time, so the variable has to exist before any test module imports the app. Postgres
# is only used when TEST_DATABASE_URL points at one; otherwise a scratch SQLite file.
_TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
if not _TEST_DATABASE_URL:
    _scratch = Path(tempfile.mkdtemp(prefix="payments-tests-")) / "payments.db"
    _TEST_DATABASE_URL = f"sqlite:///{_scratch.as_posix()}"
os.environ["PAYMENTS_DATABASE_URL"] = _TEST_DATABASE_URL
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.pop("LOG_DIR", None)

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import delete  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

from app.db import SessionLocal, engine, get_session  # noqa: E402
from app.gateway import GatewayResult, get_gateway  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base, Payment  # noqa: E402


def approved(provider_ref: str = "PAY-0123456789ab", latency_ms: int = 12) -> GatewayResult:
    return GatewayResult(
        approved=True, provider_ref=provider_ref, failure_reason=None, latency_ms=latency_ms
    )


def declined(reason: str = "insufficient_funds", latency_ms: int = 9) -> GatewayResult:
    return GatewayResult(
        approved=False, provider_ref=None, failure_reason=reason, latency_ms=latency_ms
    )


class StubGateway:
    """Returns queued results in order, then repeats the last one."""

    def __init__(self, *results: GatewayResult) -> None:
        self.queued = list(results) or [approved()]
        self.calls: list[tuple[uuid.UUID, int]] = []

    def charge(self, order_id: uuid.UUID, amount_paise: int) -> GatewayResult:
        self.calls.append((order_id, amount_paise))
        if len(self.queued) > 1:
            return self.queued.pop(0)
        return self.queued[0]


@pytest.fixture(scope="session", autouse=True)
def _schema() -> Iterator[None]:
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture(autouse=True)
def _clean_tables() -> None:
    with engine.begin() as connection:
        connection.execute(delete(Payment))


@pytest.fixture
def session() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session


@pytest.fixture
def gateway() -> StubGateway:
    return StubGateway()


@pytest.fixture
def client(session: Session, gateway: StubGateway) -> Iterator[TestClient]:
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[get_gateway] = lambda: gateway
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def order_id() -> uuid.UUID:
    return uuid.uuid4()
