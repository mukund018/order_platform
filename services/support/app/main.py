from fastapi import FastAPI

from app.api import support
from app.config import get_settings
from common.errors import install_exception_handlers
from common.health import build_health_router
from common.logging import configure_logging
from common.metrics import build_metrics_router
from common.middleware import install_platform_middleware

SERVICE_NAME = "support"


def _logs_readable() -> None:
    """Readiness for this service means the log volume is actually mounted.

    A support tool that cheerfully answers "no errors found" because it is looking at an
    empty directory is worse than one that is down, so this is a hard readiness check.
    """
    directory = get_settings().log_path
    if not directory.is_dir():
        raise FileNotFoundError(f"log directory {directory} is not mounted")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(SERVICE_NAME, level=settings.log_level, log_dir=settings.log_dir)

    app = FastAPI(
        title="support-service",
        version="0.1.0",
        summary="Log search, health and the incident board for the ops console",
    )
    app.include_router(build_health_router({"logs": _logs_readable}))
    app.include_router(build_metrics_router())
    app.include_router(support.router)

    install_exception_handlers(app)
    install_platform_middleware(app, service=SERVICE_NAME)
    # No database and no pool, so no pool metrics: this service owns nothing and only
    # reads what the others left behind.
    return app


app = create_app()
