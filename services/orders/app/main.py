from fastapi import FastAPI

from app.api import orders, reports
from app.celery_app import celery_app
from app.config import get_settings
from app.db import engine, ping_db
from app.metrics import register_order_metrics
from common.errors import install_exception_handlers
from common.health import build_health_router
from common.logging import configure_logging
from common.metrics import build_metrics_router, register_pool_metrics
from common.middleware import install_platform_middleware

SERVICE_NAME = "orders"

BROKER_TIMEOUT_S = 2.0


def _broker_ready() -> None:
    """A broker we cannot reach does not stop orders being placed, only confirmations
    being sent. It belongs in /ready, never in /health."""
    connection = celery_app.connection()
    try:
        connection.ensure_connection(max_retries=0, timeout=BROKER_TIMEOUT_S)
    finally:
        connection.release()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(SERVICE_NAME, level=settings.log_level, log_dir=settings.log_dir)

    app = FastAPI(title="orders-service", version="0.1.0")
    app.include_router(build_health_router({"database": ping_db, "broker": _broker_ready}))
    app.include_router(build_metrics_router())
    app.include_router(orders.router)
    app.include_router(reports.router)

    install_exception_handlers(app)
    install_platform_middleware(app, service=SERVICE_NAME)

    register_pool_metrics(engine, SERVICE_NAME)
    register_order_metrics()
    return app


app = create_app()
