"""structlog -> JSON, to stdout and to logs/<service>.log.

stdout is what `docker compose logs` shows; the file is what tools/logtool.py parses.
Both get the identical line so an investigation never depends on which one you opened.

Every line carries at least: timestamp, level, service, event, request_id.
"""

from __future__ import annotations

import logging
import logging.config
import sys
import time
from pathlib import Path
from typing import Any

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Order the important keys first so a raw `tail -f` is still readable.
_KEY_ORDER = ("timestamp", "level", "service", "event", "request_id")

_NOISY_LOGGERS = (
    "uvicorn.access",  # replaced by our own http_request line
    "httpx",  # one INFO line per outbound call, we log those ourselves
)


def configure_logging(
    service: str, *, level: str = "INFO", log_dir: str | Path | None = None
) -> None:
    """Call once at process start, before anything logs."""
    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        _service_adder(service),
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=[structlog.stdlib.ExtraAdder(), *shared],
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            structlog.processors.format_exc_info,
            _order_keys,
            structlog.processors.JSONRenderer(),
        ],
    )

    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_dir:
        path = Path(log_dir)
        path.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(path / f"{service}.log", encoding="utf-8"))

    root = logging.getLogger()
    for old in list(root.handlers):
        root.removeHandler(old)
    for handler in handlers:
        handler.setFormatter(formatter)
        root.addHandler(handler)
    root.setLevel(level.upper())

    for name in _NOISY_LOGGERS:
        noisy = logging.getLogger(name)
        noisy.handlers = []
        noisy.propagate = False


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)


def _service_adder(service: str) -> Any:
    def add_service(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
        event_dict["service"] = service
        return event_dict

    return add_service


def _order_keys(_: Any, __: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    ordered = {key: event_dict.pop(key) for key in _KEY_ORDER if key in event_dict}
    ordered.update(event_dict)
    return ordered


class AccessLogMiddleware:
    """One `http_request` line per request: method, path, status, duration_ms.

    Logs the *route template* (/orders/{order_id}) rather than the concrete path, so
    grouping errors by endpoint in logtool doesn't scatter across a million UUIDs.
    """

    def __init__(self, app: ASGIApp, *, exclude_paths: tuple[str, ...] = ()) -> None:
        self.app = app
        self.exclude_paths = exclude_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in self.exclude_paths:
            await self.app(scope, receive, send)
            return

        log = structlog.get_logger("access")
        started = time.perf_counter()
        status_code = 500

        async def capture_status(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, capture_status)
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 2)
            route = scope.get("route")
            log.info(
                "http_request",
                method=scope.get("method"),
                path=getattr(route, "path", None) or scope.get("path"),
                status_code=status_code,
                duration_ms=duration_ms,
            )
