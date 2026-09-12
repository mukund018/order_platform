"""Response shapes for the support API.

These exist so the ops console has a contract instead of whatever a log line happened
to contain. A log record's fixed fields are lifted out and named; everything else a
service chose to log for that event stays in `fields`, because the whole value of
structured logging is that the interesting key is different every time.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class LogRecord(BaseModel):
    timestamp: datetime | None
    service: str
    level: str
    event: str
    request_id: str | None = None
    duration_ms: float | None = None
    status_code: int | None = None
    order_id: str | None = None
    fields: dict[str, Any] = Field(default_factory=dict)


class TraceStepOut(LogRecord):
    # Milliseconds since the previous line of this trace. None on the first step, and
    # the number that actually matters: a 2000 ms gap is where the request was spent.
    gap_ms: float | None = None


class TraceOut(BaseModel):
    request_id: str
    steps: list[TraceStepOut]
    services: list[str]
    span_ms: float | None = None
    order_ids: list[str] = Field(default_factory=list)


class ErrorGroupOut(BaseModel):
    service: str
    error_code: str
    count: int
    first_seen: datetime | None
    last_seen: datetime | None
    example: LogRecord


class ErrorsOut(BaseModel):
    since: str
    cutoff: datetime
    total: int
    groups: list[ErrorGroupOut]


class SlowOut(BaseModel):
    since: str
    cutoff: datetime
    records: list[LogRecord]


class OrderStoryOut(BaseModel):
    order_id: str
    records: list[LogRecord]
    request_ids: list[str]


class ServiceHealth(BaseModel):
    name: str
    url: str
    # "ok" means the process answered /health. "ready" additionally means it can reach
    # its own dependencies - a service can be alive and unable to serve, and the ops
    # console needs to show the difference rather than a single green dot.
    live: bool
    ready: bool
    detail: str | None = None
    latency_ms: float | None = None
    checks: dict[str, str] = Field(default_factory=dict)


class OverviewOut(BaseModel):
    generated_at: datetime
    services: list[ServiceHealth]
    errors_last_15m: int
    log_lines_read: int
    malformed_lines: int


class IncidentOut(BaseModel):
    """The public half of a fault definition, never the sealed half."""

    id: str
    title: str
    category: str
    difficulty: str
    status: str
    severity: str | None = None
    tta_min: int | None = None
    ttm_min: int | None = None
    hints: int | None = None
    rca_score: str | None = None


class IncidentsOut(BaseModel):
    incidents: list[IncidentOut]
    closed: int
    total: int
