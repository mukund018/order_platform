from collections.abc import Iterator

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings


def _build_engine() -> Engine:
    settings = get_settings()
    url = settings.database_url
    if url.startswith("sqlite"):
        # SQLite has no server-side pool to size; the test suite uses it.
        return create_engine(url, pool_pre_ping=True, future=True)
    return create_engine(
        url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
        future=True,
    )


engine = _build_engine()

# expire_on_commit=False: the request flow commits several times and keeps using the
# same Order object afterwards, and the response is serialised after the session closes.
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def get_session() -> Iterator[Session]:
    """Request-scoped session. The flow commits at each step; anything unhandled after
    the last commit is rolled back rather than left for close() to discard quietly."""
    session = SessionLocal()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def ping_db() -> None:
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
