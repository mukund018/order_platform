import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from common.errors import AppError, NotFoundError, OutOfStockError, install_exception_handlers
from common.health import build_health_router
from common.metrics import build_metrics_router
from common.middleware import install_platform_middleware
from common.request_id import get_request_id


class Boom(Exception):
    pass


@pytest.fixture
def make_app():
    """Builds a miniature service wired exactly the way the real ones are."""

    def _make(checks=None):
        app = FastAPI()

        @app.get("/things/{thing_id}")
        def read_thing(thing_id: str) -> dict[str, str | None]:
            if thing_id == "missing":
                raise NotFoundError("no thing called missing")
            if thing_id == "empty":
                raise OutOfStockError(
                    "SKU-0003 has 2 units, 5 requested", details={"sku": "SKU-0003"}
                )
            if thing_id == "teapot":
                raise AppError("short and stout", code="I_AM_A_TEAPOT", status_code=418)
            if thing_id == "boom":
                raise Boom("unhandled")
            return {"thing_id": thing_id, "request_id": get_request_id()}

        @app.post("/things")
        def create_thing(body: dict) -> dict:
            return body

        app.include_router(build_health_router(checks))
        app.include_router(build_metrics_router())
        install_exception_handlers(app)
        install_platform_middleware(app, service="testsvc")
        return app

    return _make


@pytest.fixture
def client(make_app):
    with TestClient(make_app(), raise_server_exceptions=False) as c:
        yield c
