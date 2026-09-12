"""Correlation IDs.

One request can touch orders -> inventory -> payments -> a celery worker. Without a
shared id you end up grepping four log files by timestamp and guessing. Every entry
point puts an id in a ContextVar, every log line picks it up automatically, and every
outbound call forwards it in the X-Request-ID header.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import httpx
import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_HEADER = "X-Request-ID"


def new_request_id() -> str:
    return uuid.uuid4().hex


def get_request_id() -> str | None:
    """Current request id, or None outside a request/task."""
    return structlog.contextvars.get_contextvars().get("request_id")


def bind_request_id(request_id: str | None = None) -> str:
    """Bind an id to the logging context and return it. Generates one if not given."""
    request_id = request_id or new_request_id()
    structlog.contextvars.bind_contextvars(request_id=request_id)
    return request_id


def clear_context() -> None:
    structlog.contextvars.clear_contextvars()


class RequestContextMiddleware:
    """Pure ASGI middleware: read or mint X-Request-ID and echo it back.

    Written against the raw ASGI interface rather than BaseHTTPMiddleware because
    BaseHTTPMiddleware runs the handler in a separate task, which loses ContextVars
    set inside it (the exact thing we are trying to keep).
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = _header_value(scope, REQUEST_ID_HEADER)
        request_id = bind_request_id(incoming)
        scope["request_id"] = request_id

        async def send_with_header(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                headers.append(
                    (REQUEST_ID_HEADER.lower().encode("latin-1"), request_id.encode("latin-1"))
                )
            await send(message)

        try:
            await self.app(scope, receive, send_with_header)
        finally:
            clear_context()


def _header_value(scope: Scope, name: str) -> str | None:
    wanted = name.lower().encode("latin-1")
    for key, value in scope.get("headers", []):
        if key.lower() == wanted:
            decoded = value.decode("latin-1").strip()
            # Never trust an arbitrarily long header into our log lines.
            return decoded[:64] or None
    return None


def _forward_request_id(request: httpx.Request) -> None:
    request_id = get_request_id()
    if request_id and REQUEST_ID_HEADER not in request.headers:
        request.headers[REQUEST_ID_HEADER] = request_id


async def _forward_request_id_async(request: httpx.Request) -> None:
    _forward_request_id(request)


def httpx_event_hooks() -> dict[str, list[Callable[[httpx.Request], None]]]:
    """Pass to httpx.Client(event_hooks=...) so every outbound call carries the id."""
    return {"request": [_forward_request_id]}


def httpx_async_event_hooks() -> dict[str, list[Callable[[httpx.Request], Awaitable[None]]]]:
    """Same, for httpx.AsyncClient."""
    return {"request": [_forward_request_id_async]}
