# ruff: noqa: E402 - app/config.py reads the environment at import time, and importing
# anything under app/ imports it, so the variables have to be set before that happens.
import json
import tempfile
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

_LOGS = Path(tempfile.mkdtemp(prefix="support-tests-")) / "logs"
_LOGS.mkdir(parents=True)
_INCIDENTS = Path(tempfile.mkdtemp(prefix="support-incidents-"))

import os

os.environ["LOG_DIR"] = str(_LOGS)
os.environ["SUPPORT_INCIDENTS_DIR"] = str(_INCIDENTS)
os.environ.setdefault("LOG_LEVEL", "WARNING")

from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app

NOW = datetime(2026, 9, 12, 10, 0, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _clean() -> Iterator[None]:
    """Each test writes the log files it needs and nothing else sees them."""
    for path in _LOGS.glob("*.log"):
        path.unlink()
    for path in sorted(_INCIDENTS.rglob("*"), reverse=True):
        path.unlink() if path.is_file() else path.rmdir()
    # LOG_DIR is read through a cached Settings, so the cache has to go with it.
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def logs() -> "LogWriter":
    return LogWriter(_LOGS)


@pytest.fixture
def incidents_dir() -> Path:
    return _INCIDENTS


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


class LogWriter:
    """Writes the newline-delimited JSON the real services write."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        # One base instant per test, so `offset_s` means exactly what it says. Reading
        # the clock per line would fold the test's own runtime into every gap.
        self.base = datetime.now(UTC)

    def write(self, service: str, *records: dict[str, Any]) -> None:
        path = self.directory / f"{service}.log"
        with path.open("a", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record) + "\n")

    def line(
        self,
        service: str,
        *,
        offset_s: float = 0.0,
        level: str = "info",
        event: str = "http_request",
        at: datetime | None = None,
        **fields: Any,
    ) -> dict[str, Any]:
        moment = (at or self.base) + timedelta(seconds=offset_s)
        record = {
            "timestamp": moment.isoformat().replace("+00:00", "Z"),
            "level": level,
            "service": service,
            "event": event,
        }
        record.update(fields)
        self.write(service, record)
        return record


def new_request_id() -> str:
    return uuid.uuid4().hex
