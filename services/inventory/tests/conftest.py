# ruff: noqa: E402 - the database url has to be in the environment before app.config
# is imported, and app.config is imported the moment anything under app/ is.
import os
import tempfile
from collections.abc import Callable, Iterator
from pathlib import Path

import fakeredis
import pytest

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
_SCRATCH = Path(tempfile.mkdtemp(prefix="inventory-tests-"))

os.environ["INVENTORY_DATABASE_URL"] = (
    TEST_DATABASE_URL or f"sqlite+pysqlite:///{_SCRATCH / 'inventory.db'}"
)
os.environ.setdefault("TRACKED_SKUS", "SKU-0001,SKU-0002")
os.environ.pop("LOG_DIR", None)

from fastapi.testclient import TestClient
from sqlalchemy import delete
from sqlalchemy.orm import Session

from app import cache as cache_module
from app.cache import ProductCache
from app.db import SessionLocal, engine, get_session
from app.main import app
from app.models import Base, Product
from common.testing import require_test_database


@pytest.fixture(scope="session", autouse=True)
def schema() -> Iterator[None]:
    require_test_database(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture(autouse=True)
def clean_tables() -> None:
    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.execute(delete(table))


@pytest.fixture(autouse=True)
def fake_cache() -> Iterator[ProductCache]:
    cache = ProductCache(fakeredis.FakeRedis(decode_responses=True), ttl=60)
    cache_module.set_cache(cache)
    yield cache
    cache_module.set_cache(None)


@pytest.fixture
def session() -> Iterator[Session]:
    with SessionLocal() as session:
        yield session


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
def make_product(session: Session) -> Callable[..., Product]:
    def factory(
        sku: str = "SKU-0001",
        *,
        name: str = "Widget",
        price_paise: int = 19900,
        stock: int = 10,
    ) -> Product:
        product = Product(sku=sku, name=name, price_paise=price_paise, stock=stock)
        session.add(product)
        session.commit()
        return product

    return factory
