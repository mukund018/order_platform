"""Liveness, readiness and the standard envelope for this service itself."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings


def test_health_never_touches_a_dependency(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_passes_when_the_log_volume_is_mounted(client: TestClient) -> None:
    body = client.get("/ready").json()

    assert body["status"] == "ready"
    assert body["checks"]["logs"] == "ok"


def test_ready_fails_when_the_log_volume_is_missing(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A support tool reading an unmounted directory would answer "no errors found" to
    every question, which is the most dangerous thing it could do."""
    monkeypatch.setenv("LOG_DIR", "/nowhere-at-all")
    get_settings.cache_clear()

    response = client.get("/ready")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "DEPENDENCY_UNAVAILABLE"


def test_metrics_are_exposed(client: TestClient) -> None:
    body = client.get("/metrics").text

    assert "http_requests_total" in body


def test_every_response_carries_a_request_id(client: TestClient) -> None:
    response = client.get("/health")

    assert response.headers["X-Request-ID"]
