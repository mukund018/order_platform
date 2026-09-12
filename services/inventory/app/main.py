from fastapi import FastAPI

from app.api import products, reservations
from app.cache import get_cache
from app.config import get_settings
from app.db import engine, ping_db
from app.metrics import register_stock_metrics
from common.errors import install_exception_handlers
from common.health import build_health_router
from common.logging import configure_logging
from common.metrics import build_metrics_router, register_pool_metrics
from common.middleware import install_platform_middleware

SERVICE_NAME = "inventory"


def _redis_ready() -> None:
    get_cache().ping()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(SERVICE_NAME, level=settings.log_level, log_dir=settings.log_dir)

    app = FastAPI(title="inventory-service", version="0.1.0")
    app.include_router(build_health_router({"database": ping_db, "redis": _redis_ready}))
    app.include_router(build_metrics_router())
    app.include_router(products.router)
    app.include_router(reservations.router)

    install_exception_handlers(app)
    install_platform_middleware(app, service=SERVICE_NAME)

    register_pool_metrics(engine, SERVICE_NAME)
    register_stock_metrics()
    return app


app = create_app()
