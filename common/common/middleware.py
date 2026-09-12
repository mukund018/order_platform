"""The middleware stack, assembled once so all three services behave identically.

Order matters. Outermost to innermost:

    RequestContextMiddleware   mint/accept X-Request-ID -> everything below can log it
    UnhandledErrorMiddleware   turn an escaped exception into the standard error body
    AccessLogMiddleware        one http_request line, with the final status
    MetricsMiddleware          http_requests_total / http_request_duration_seconds
    (router)

The catch-all lives here rather than in an @app.exception_handler(Exception), because
Starlette runs that handler in ServerErrorMiddleware - which is installed *outside*
our stack, so by the time it builds a response the request id has already been
cleared and the body comes out with "request_id": null.
"""

from __future__ import annotations

import structlog
from fastapi import FastAPI
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .errors import error_response
from .logging import AccessLogMiddleware
from .metrics import MetricsMiddleware
from .request_id import RequestContextMiddleware

log = structlog.get_logger(__name__)


class UnhandledErrorMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = False

        async def track_start(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, receive, track_start)
        except Exception as exc:
            log.exception(
                "unhandled_exception",
                error_code="INTERNAL_ERROR",
                exc_type=type(exc).__name__,
                path=scope.get("path"),
                method=scope.get("method"),
            )
            if started:
                # Headers are already on the wire; we cannot replace the body.
                raise
            response = error_response(500, "INTERNAL_ERROR", "internal server error")
            await response(scope, receive, send)


def install_platform_middleware(
    app: FastAPI, *, service: str, exclude_paths: tuple[str, ...] = ("/metrics", "/health")
) -> None:
    """add_middleware prepends, so these are registered inner-first."""
    app.add_middleware(MetricsMiddleware, service=service, exclude_paths=exclude_paths)
    app.add_middleware(AccessLogMiddleware, exclude_paths=exclude_paths)
    app.add_middleware(UnhandledErrorMiddleware)
    app.add_middleware(RequestContextMiddleware)
