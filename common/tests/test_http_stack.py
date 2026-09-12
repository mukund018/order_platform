import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from common.errors import install_exception_handlers
from common.middleware import install_platform_middleware
from common.request_id import REQUEST_ID_HEADER, bind_request_id, clear_context, httpx_event_hooks


def test_incoming_request_id_is_used_and_echoed(client):
    response = client.get("/things/abc", headers={REQUEST_ID_HEADER: "caller-supplied-id"})

    assert response.status_code == 200
    assert response.json()["request_id"] == "caller-supplied-id"
    assert response.headers[REQUEST_ID_HEADER] == "caller-supplied-id"


def test_request_id_is_generated_when_the_caller_sends_none(client):
    response = client.get("/things/abc")

    generated = response.headers[REQUEST_ID_HEADER]
    assert generated
    assert response.json()["request_id"] == generated


def test_request_ids_differ_between_requests(client):
    first = client.get("/things/abc").headers[REQUEST_ID_HEADER]
    second = client.get("/things/abc").headers[REQUEST_ID_HEADER]

    assert first != second


def test_an_over_long_request_id_is_truncated(client):
    response = client.get("/things/abc", headers={REQUEST_ID_HEADER: "x" * 500})

    assert len(response.json()["request_id"]) == 64


@pytest.mark.parametrize(
    ("path", "status", "code"),
    [
        ("/things/missing", 404, "NOT_FOUND"),
        ("/things/empty", 409, "OUT_OF_STOCK"),
        ("/things/teapot", 418, "I_AM_A_TEAPOT"),
        ("/things/boom", 500, "INTERNAL_ERROR"),
        ("/nothing/here", 404, "NOT_FOUND"),
    ],
)
def test_every_error_uses_the_standard_envelope(client, path, status, code):
    response = client.get(path)

    assert response.status_code == status
    error = response.json()["error"]
    assert error["code"] == code
    assert error["message"]
    assert error["request_id"] == response.headers[REQUEST_ID_HEADER]


def test_unhandled_exception_keeps_its_request_id():
    # Regression: registering a handler for Exception puts it in ServerErrorMiddleware,
    # which runs outside our stack after the request id has been cleared, so the body
    # came back with "request_id": null. UnhandledErrorMiddleware exists to prevent that.
    app = FastAPI()

    @app.get("/kaboom")
    def kaboom() -> None:
        raise RuntimeError("nope")

    install_exception_handlers(app)
    install_platform_middleware(app, service="testsvc")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/kaboom", headers={REQUEST_ID_HEADER: "trace-this"})

    assert response.status_code == 500
    assert response.json()["error"]["request_id"] == "trace-this"


def test_error_details_are_included_when_present(client):
    error = client.get("/things/empty").json()["error"]

    assert error["details"] == {"sku": "SKU-0003"}


def test_error_details_are_omitted_when_empty(client):
    assert "details" not in client.get("/things/missing").json()["error"]


def test_request_validation_failure_reports_the_field(client):
    response = client.post(
        "/things", content=b"not json", headers={"Content-Type": "application/json"}
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_health_never_touches_dependencies(make_app):
    def always_broken() -> None:
        raise RuntimeError("database is gone")

    with TestClient(make_app({"database": always_broken})) as client:
        assert client.get("/health").json() == {"status": "ok"}


def test_ready_reports_each_check(make_app):
    with TestClient(make_app({"database": lambda: None, "redis": lambda: None})) as client:
        body = client.get("/ready").json()

    assert body == {"status": "ready", "checks": {"database": "ok", "redis": "ok"}}


def test_ready_returns_503_and_names_the_broken_dependency(make_app):
    def broken() -> None:
        raise ConnectionError("connection refused")

    with TestClient(make_app({"database": lambda: None, "redis": broken})) as client:
        response = client.get("/ready")

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "DEPENDENCY_UNAVAILABLE"
    assert "redis" in error["message"]
    assert error["details"]["checks"]["database"] == "ok"
    assert error["details"]["checks"]["redis"].startswith("error: ConnectionError")


def test_ready_supports_async_checks(make_app):
    async def slow_but_fine() -> None:
        return None

    with TestClient(make_app({"database": slow_but_fine})) as client:
        assert client.get("/ready").status_code == 200


def test_metrics_label_the_route_template_not_the_raw_path(client):
    # If the concrete path leaked into the label, every id would become its own series.
    client.get("/things/one")
    client.get("/things/two")

    body = client.get("/metrics").text

    assert 'path="/things/{thing_id}"' in body
    assert 'path="/things/one"' not in body


def test_metrics_record_the_status_code(client):
    client.get("/things/missing")

    body = client.get("/metrics").text

    assert 'service="testsvc"' in body
    assert 'status="404"' in body
    assert "http_request_duration_seconds_bucket" in body


def test_httpx_hook_forwards_the_current_request_id():
    sent = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent["header"] = request.headers.get(REQUEST_ID_HEADER)
        return httpx.Response(200)

    bind_request_id("outbound-id")
    try:
        with httpx.Client(
            transport=httpx.MockTransport(handler), event_hooks=httpx_event_hooks()
        ) as client:
            client.get("http://inventory/products")
    finally:
        clear_context()

    assert sent["header"] == "outbound-id"


def test_httpx_hook_is_a_no_op_outside_a_request():
    sent = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent["header"] = request.headers.get(REQUEST_ID_HEADER)
        return httpx.Response(200)

    clear_context()
    with httpx.Client(
        transport=httpx.MockTransport(handler), event_hooks=httpx_event_hooks()
    ) as client:
        client.get("http://inventory/products")

    assert sent["header"] is None
