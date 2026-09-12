"""One error shape for every non-2xx response in the platform.

    {"error": {"code": "OUT_OF_STOCK", "message": "...", "request_id": "..."}}

Callers (including our own httpx clients) can rely on `code` being a stable string,
so orders-service can branch on OUT_OF_STOCK without parsing English.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .request_id import get_request_id

log = structlog.get_logger(__name__)


class AppError(Exception):
    """Base class for anything we deliberately turn into an HTTP error."""

    code = "INTERNAL_ERROR"
    status_code = 500

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        self.details = details or {}


class NotFoundError(AppError):
    code = "NOT_FOUND"
    status_code = 404


class ConflictError(AppError):
    code = "CONFLICT"
    status_code = 409


class ValidationFailedError(AppError):
    code = "VALIDATION_ERROR"
    status_code = 422


class OutOfStockError(AppError):
    code = "OUT_OF_STOCK"
    status_code = 409


class DependencyUnavailableError(AppError):
    """A dependency we own is down (DB, Redis). Used by /ready."""

    code = "DEPENDENCY_UNAVAILABLE"
    status_code = 503


class UpstreamTimeoutError(AppError):
    """Another service did not answer inside our timeout."""

    code = "UPSTREAM_TIMEOUT"
    status_code = 504


class UpstreamError(AppError):
    """Another service answered, but with something we cannot use."""

    code = "UPSTREAM_ERROR"
    status_code = 502


# Used when an upstream returns our standard envelope and we want to keep its code.
_STATUS_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMITED",
    500: "INTERNAL_ERROR",
    502: "UPSTREAM_ERROR",
    503: "DEPENDENCY_UNAVAILABLE",
    504: "UPSTREAM_TIMEOUT",
}


def error_body(code: str, message: str, *, details: dict[str, Any] | None = None) -> dict[str, Any]:
    error: dict[str, Any] = {
        "code": code,
        "message": message,
        "request_id": get_request_id(),
    }
    if details:
        error["details"] = details
    return {"error": error}


def error_response(
    status_code: int, code: str, message: str, *, details: dict[str, Any] | None = None
) -> JSONResponse:
    # The X-Request-ID response header is set by RequestContextMiddleware on the way out,
    # for every response. Setting it here as well appends a second copy of it.
    return JSONResponse(status_code=status_code, content=error_body(code, message, details=details))


def install_exception_handlers(app: FastAPI) -> None:
    """Register handlers so nothing can leak a default HTML/JSON error page."""

    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        # Client mistakes are noise at error level; our own faults are not.
        level = "warning" if exc.status_code < 500 else "error"
        getattr(log, level)(
            "request_failed",
            error_code=exc.code,
            status_code=exc.status_code,
            reason=exc.message,
            **exc.details,
        )
        return error_response(exc.status_code, exc.code, exc.message, details=exc.details)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        first = exc.errors()[0] if exc.errors() else {}
        location = ".".join(str(part) for part in first.get("loc", ())[1:]) or "body"
        message = f"{location}: {first.get('msg', 'invalid request')}"
        log.warning("request_invalid", error_code="VALIDATION_ERROR", reason=message)
        return error_response(422, "VALIDATION_ERROR", message)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_CODES.get(exc.status_code, "HTTP_ERROR")
        message = exc.detail if isinstance(exc.detail, str) else code.replace("_", " ").lower()
        return error_response(exc.status_code, code, message)

    # Anything that escapes the router is caught by UnhandledErrorMiddleware, which
    # still has the request id bound. This handler only covers failures further out
    # (in the middleware stack itself) so that even then we never return HTML.
    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception(
            "unhandled_exception", error_code="INTERNAL_ERROR", exc_type=type(exc).__name__
        )
        return error_response(500, "INTERNAL_ERROR", "internal server error")
