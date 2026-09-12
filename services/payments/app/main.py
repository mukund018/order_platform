from fastapi import FastAPI

from app.api.payments import router as payments_router
from app.config import get_settings
from app.db import engine, ping_db
from common.errors import install_exception_handlers
from common.health import build_health_router
from common.logging import configure_logging
from common.metrics import build_metrics_router, register_pool_metrics
from common.middleware import install_platform_middleware

SERVICE_NAME = "payments"


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(SERVICE_NAME, level=settings.log_level, log_dir=settings.log_dir)

    app = FastAPI(title="payments-service", version="0.1.0")
    app.include_router(build_health_router({"database": ping_db}))
    app.include_router(build_metrics_router())
    app.include_router(payments_router)

    install_exception_handlers(app)
    install_platform_middleware(app, service=SERVICE_NAME)
    register_pool_metrics(engine, SERVICE_NAME)
    return app


app = create_app()
