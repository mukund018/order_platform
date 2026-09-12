"""The support API over a log directory we control line by line."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import respx
from fastapi.testclient import TestClient

from tests.conftest import LogWriter, new_request_id


def test_trace_follows_one_request_across_services(client: TestClient, logs: LogWriter) -> None:
    request_id = new_request_id()
    logs.line("orders", offset_s=0.0, event="http_request", request_id=request_id, duration_ms=412)
    logs.line("inventory", offset_s=0.05, event="http_request", request_id=request_id)
    logs.line("payments", offset_s=0.2, event="http_request", request_id=request_id)
    # A different request in the same files must not leak into the trace.
    logs.line("orders", offset_s=0.1, request_id=new_request_id())

    body = client.get(f"/support/trace/{request_id}").json()

    assert [step["service"] for step in body["steps"]] == ["orders", "inventory", "payments"]
    assert body["services"] == ["orders", "inventory", "payments"]
    # The first step has nothing to measure against; every later one does.
    assert body["steps"][0]["gap_ms"] is None
    assert body["steps"][1]["gap_ms"] == 50.0
    assert body["span_ms"] == 200.0


def test_trace_lifts_named_fields_and_keeps_the_rest(client: TestClient, logs: LogWriter) -> None:
    request_id = new_request_id()
    logs.line(
        "orders",
        event="upstream_timeout",
        level="error",
        request_id=request_id,
        order_id="11111111-2222-3333-4444-555555555555",
        duration_ms=2005.0,
        status_code=504,
        upstream="inventory",
        timeout_s=2.0,
    )

    step = client.get(f"/support/trace/{request_id}").json()["steps"][0]

    assert step["order_id"] == "11111111-2222-3333-4444-555555555555"
    assert step["duration_ms"] == 2005.0
    assert step["status_code"] == 504
    # Whatever the service chose to log for this event survives verbatim - that is the
    # whole point of structured logs, and a fixed schema here would throw it away.
    assert step["fields"] == {"upstream": "inventory", "timeout_s": 2.0}


def test_trace_for_an_unknown_request_is_a_404_envelope(client: TestClient) -> None:
    response = client.get("/support/trace/does-not-exist")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_errors_group_by_service_and_code(client: TestClient, logs: LogWriter) -> None:
    for _ in range(3):
        logs.line("orders", level="error", event="upstream_timeout", error_code="UPSTREAM_TIMEOUT")
    logs.line("inventory", level="warning", event="reservation_rejected", error_code="OUT_OF_STOCK")
    logs.line("orders", level="info", event="order_created")

    body = client.get("/support/errors?since=15m").json()

    assert body["total"] == 4
    assert [(g["service"], g["error_code"], g["count"]) for g in body["groups"]] == [
        ("orders", "UPSTREAM_TIMEOUT", 3),
        ("inventory", "OUT_OF_STOCK", 1),
    ]
    assert body["groups"][0]["example"]["event"] == "upstream_timeout"


def test_errors_ignore_lines_outside_the_window(client: TestClient, logs: LogWriter) -> None:
    logs.line("orders", level="error", error_code="OLD", offset_s=-3600)
    logs.line("orders", level="error", error_code="RECENT")

    codes = [g["error_code"] for g in client.get("/support/errors?since=15m").json()["groups"]]

    assert codes == ["RECENT"]


def test_a_bad_window_is_a_validation_error(client: TestClient) -> None:
    response = client.get("/support/errors?since=banana")

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_slow_ranks_by_duration(client: TestClient, logs: LogWriter) -> None:
    for duration in (120, 2400, 800):
        logs.line("orders", duration_ms=duration)
    # Not an access log line, so it is not a request and cannot be the slowest one.
    logs.line("orders", event="cache_read_failed", duration_ms=9999)

    body = client.get("/support/slow?since=15m&top=2").json()

    assert [record["duration_ms"] for record in body["records"]] == [2400.0, 800.0]


def test_order_story_finds_the_id_wherever_it_appears(client: TestClient, logs: LogWriter) -> None:
    order_id = "9f8e7d6c-0000-1111-2222-333344445555"
    first, second = new_request_id(), new_request_id()
    logs.line(
        "orders", offset_s=0.0, event="order_created", order_id=order_id, request_id=first
    )
    # Nested inside details rather than in a named field, which is where an order id
    # usually hides on the failure paths.
    logs.line(
        "inventory",
        offset_s=0.04,
        event="stock_reserved",
        request_id=second,
        details={"order_id": order_id},
    )
    logs.line("orders", offset_s=0.08, event="order_created", order_id="unrelated")

    body = client.get(f"/support/order/{order_id}").json()

    assert len(body["records"]) == 2
    # Time order across services, which is the order you want to read a story in.
    assert body["request_ids"] == [first, second]


def test_overview_reports_a_service_that_will_not_answer(client: TestClient) -> None:
    with respx.mock(assert_all_called=False) as router:
        router.get("http://orders:8001/ready").mock(
            return_value=httpx.Response(200, json={"status": "ready", "checks": {"database": "ok"}})
        )
        router.get("http://inventory:8002/ready").mock(
            return_value=httpx.Response(
                503,
                json={
                    "error": {
                        "code": "DEPENDENCY_UNAVAILABLE",
                        "message": "not ready: redis",
                        "details": {"checks": {"database": "ok", "redis": "error: RedisError"}},
                    }
                },
            )
        )
        router.get("http://payments:8003/ready").mock(side_effect=httpx.ConnectError("refused"))

        body = client.get("/support/overview").json()

    services = {item["name"]: item for item in body["services"]}
    assert services["orders"]["live"] and services["orders"]["ready"]
    # Alive but refusing traffic is a third state, and the console has to see it.
    assert services["inventory"]["live"] and not services["inventory"]["ready"]
    assert services["inventory"]["checks"]["redis"].startswith("error")
    assert not services["payments"]["live"]
    assert services["payments"]["detail"] == "ConnectError"


def test_incidents_never_expose_the_sealed_half(
    client: TestClient, incidents_dir: Path
) -> None:
    faults = incidents_dir / "faults"
    faults.mkdir(parents=True)
    (faults / "INC-001.json").write_text(
        json.dumps(
            {
                "id": "INC-001",
                "title": "Orders stall at PENDING",
                "category": "Configuration / environment",
                "difficulty": "guided",
                "ticket": "...",
                "spoiler": "c2VjcmV0",
            }
        ),
        encoding="utf-8",
    )
    (incidents_dir / "INDEX.md").write_text(
        "| ID | Title | Sev |\n|---|---|---|\n"
        "| INC-001 | Orders stall at PENDING | SEV2 | Configuration | 4 | 21 | 1 | 8/10 |\n",
        encoding="utf-8",
    )

    response = client.get("/support/incidents")
    body = response.json()

    assert "spoiler" not in response.text
    assert body["closed"] == 1
    assert body["incidents"][0]["severity"] == "SEV2"
    assert body["incidents"][0]["ttm_min"] == 21
    assert body["incidents"][0]["rca_score"] == "8/10"


def test_an_incident_with_no_index_row_reports_its_state_on_disk(
    client: TestClient, incidents_dir: Path
) -> None:
    faults = incidents_dir / "faults"
    faults.mkdir(parents=True)
    (faults / "INC-002.json").write_text(
        json.dumps(
            {
                "id": "INC-002",
                "title": "Restocked products keep selling zero",
                "category": "Caching",
                "difficulty": "guided",
                "ticket": "...",
                "spoiler": "x",
            }
        ),
        encoding="utf-8",
    )
    (incidents_dir / "INC-002").mkdir()
    (incidents_dir / "INC-002" / "investigation.md").write_text("working on it", encoding="utf-8")

    body = client.get("/support/incidents").json()

    assert body["incidents"][0]["status"] == "in progress"
    assert body["closed"] == 0
