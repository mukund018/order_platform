"""Prometheus instrumentation shared by all three services.

RED on the HTTP surface (Rate, Errors, Duration) plus the connection pool, which is
the thing that quietly runs out first when something downstream gets slow.

The `path` label is the route template, never the raw URL - otherwise every order id
becomes its own time series and Prometheus falls over.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    REGISTRY,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from prometheus_client.core import GaugeMetricFamily
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_COUNT = Counter(
    "http_requests_total",
    "HTTP requests handled",
    ["service", "method", "path", "status"],
)

REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request duration",
    ["service", "method", "path"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 0.75, 1.0, 2.5, 5.0, 10.0),
)

REQUESTS_IN_PROGRESS = Gauge(
    "http_requests_in_progress",
    "HTTP requests currently being handled",
    ["service"],
)


class MetricsMiddleware:
    def __init__(self, app: ASGIApp, *, service: str, exclude_paths: tuple[str, ...] = ()) -> None:
        self.app = app
        self.service = service
        self.exclude_paths = exclude_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") in self.exclude_paths:
            await self.app(scope, receive, send)
            return

        status_code = 500
        started = time.perf_counter()

        async def capture_status(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        REQUESTS_IN_PROGRESS.labels(self.service).inc()
        try:
            await self.app(scope, receive, capture_status)
        finally:
            REQUESTS_IN_PROGRESS.labels(self.service).dec()
            route = scope.get("route")
            path = getattr(route, "path", None) or "<unmatched>"
            method = scope.get("method", "UNKNOWN")
            REQUEST_DURATION.labels(self.service, method, path).observe(
                time.perf_counter() - started
            )
            REQUEST_COUNT.labels(self.service, method, path, str(status_code)).inc()


def build_metrics_router() -> APIRouter:
    router = APIRouter(tags=["metrics"])

    @router.get("/metrics", include_in_schema=False)
    async def metrics() -> Response:
        return Response(generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)

    return router


class _PoolCollector:
    """Reads SQLAlchemy pool counters at scrape time instead of polling them."""

    def __init__(self, engine: Any, service: str) -> None:
        self.engine = engine
        self.service = service

    def collect(self) -> Iterator[GaugeMetricFamily]:
        pool = self.engine.pool
        connections = GaugeMetricFamily(
            "db_pool_connections",
            "SQLAlchemy connection pool slots by state",
            labels=["service", "state"],
        )
        for state, getter in (
            ("in_use", "checkedout"),
            ("idle", "checkedin"),
            ("overflow", "overflow"),
        ):
            fn = getattr(pool, getter, None)
            if fn is not None:
                connections.add_metric([self.service, state], float(fn()))
        yield connections

        size = getattr(pool, "size", None)
        if size is not None:
            capacity = GaugeMetricFamily(
                "db_pool_size", "Configured SQLAlchemy pool size", labels=["service"]
            )
            capacity.add_metric([self.service], float(size()))
            yield capacity


def register_pool_metrics(engine: Any, service: str) -> None:
    REGISTRY.register(_PoolCollector(engine, service))
