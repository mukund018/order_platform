from collections.abc import Iterator

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings


def _build_engine() -> Engine:
    settings = get_settings()
    if settings.database_url.startswith("sqlite"):
        # The test suite runs on a file-backed SQLite database; QueuePool options
        # do not apply to it and SQLAlchemy rejects them.
        return create_engine(settings.database_url, future=True)
    return create_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
        future=True,
    )


engine = _build_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    """Request-scoped session. The endpoint commits; anything unhandled rolls back."""
    session = SessionLocal()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def ping_db() -> None:
    """Readiness probe: raises if the database is unreachable."""
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
