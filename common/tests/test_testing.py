import pytest
from sqlalchemy import create_engine

from common.testing import UnsafeTestDatabaseError, require_test_database


def test_sqlite_is_always_allowed() -> None:
    engine = create_engine("sqlite:///:memory:")

    require_test_database(engine)  # must not raise


def test_a_database_named_with_a_test_suffix_is_allowed() -> None:
    engine = create_engine("postgresql+psycopg://app:app@localhost:5432/inventory_test")

    require_test_database(engine)  # must not raise


def test_a_live_looking_database_name_is_refused() -> None:
    engine = create_engine("postgresql+psycopg://app:app@localhost:5432/inventory_db")

    with pytest.raises(UnsafeTestDatabaseError, match="inventory_db"):
        require_test_database(engine)
