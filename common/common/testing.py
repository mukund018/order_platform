"""Guards for test fixtures that touch real database schema.

A session-scoped pytest fixture that calls `Base.metadata.drop_all(engine)` at
teardown is one misdirected `TEST_DATABASE_URL` away from dropping a live
database's tables — which is exactly what happened once during this project's
own development (inventory_db, orders_db and payments_db all lost their tables
when the Postgres-only concurrency tests were pointed at the live databases
instead of the dedicated `*_test` ones). This module is the guardrail that
incident should have left behind from the start.
"""

from __future__ import annotations

from sqlalchemy.engine import Engine


class UnsafeTestDatabaseError(RuntimeError):
    """Raised when a test fixture is about to create/drop schema on a database
    that does not look like a disposable test database."""


def require_test_database(engine: Engine) -> None:
    """Raise unless `engine` looks like a throwaway test database.

    SQLite is always safe here — every service's test suite only ever points it at
    a fresh temp file. A PostgreSQL database must have a name ending in `_test`;
    anything else (in particular a live `*_db` database) is refused before a single
    `CREATE TABLE` or `DROP TABLE` runs.
    """
    if engine.dialect.name == "sqlite":
        return

    database = engine.url.database or ""
    if not database.endswith("_test"):
        raise UnsafeTestDatabaseError(
            f"refusing to create/drop schema on database {database!r} — its name "
            "does not end in '_test'. Point TEST_DATABASE_URL at the dedicated "
            "*_test database, never at a live one."
        )
