"""What the support API actually does.

Three sources, one shape: the JSON log files on the shared volume (via
`common.logsearch`, the same code `tools/logtool.py` uses), the other services' own
/health and /ready endpoints, and the incident files on disk.

Nothing here holds state. Every call re-reads, because the point of a support tool is
to tell you what is true now, not what was true when it started.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings
from app.schemas import (
    ErrorGroupOut,
    ErrorsOut,
    IncidentOut,
    IncidentsOut,
    LogRecord,
    OrderStoryOut,
    OverviewOut,
    ServiceHealth,
    SlowOut,
    TraceOut,
    TraceStepOut,
)
from common.errors import NotFoundError, ValidationFailedError
from common.logging import get_logger
from common.logsearch import (
    STANDARD_KEYS,
    ErrorGroup,
    build_trace,
    filter_since,
    find_order,
    group_errors,
    parse_duration,
    parse_timestamp,
    rank_slow,
    read_records,
    sort_records,
)

log = get_logger(__name__)

# Keys the response model names in its own right. Anything else stays in `fields`.
LIFTED_KEYS = STANDARD_KEYS | {"duration_ms", "status_code", "order_id"}

ERROR_WINDOW = "15m"


# --------------------------------------------------------------------------- logs


def _load(settings: Settings) -> tuple[list[dict[str, Any]], int]:
    directory = settings.log_path
    if not directory.is_dir():
        # An empty log directory is a legitimate state (nothing has run yet) and must
        # not read as a broken support service.
        log.warning("log_dir_missing", log_dir=str(directory))
        return [], 0
    records, malformed = read_records(directory)
    if len(records) > settings.max_records:
        records = sort_records(records)[-settings.max_records :]
    return records, malformed


def _to_record(record: dict[str, Any]) -> LogRecord:
    return LogRecord(
        timestamp=parse_timestamp(record.get("timestamp")),
        service=str(record.get("service") or "unknown"),
        level=str(record.get("level") or "info"),
        event=str(record.get("event") or ""),
        request_id=_as_str(record.get("request_id")),
        duration_ms=_as_float(record.get("duration_ms")),
        status_code=_as_int(record.get("status_code")),
        order_id=_as_str(record.get("order_id")),
        fields={k: v for k, v in record.items() if k not in LIFTED_KEYS},
    )


def trace(settings: Settings, request_id: str) -> TraceOut:
    """One request id, everywhere it was logged, in time order."""
    records, _ = _load(settings)
    steps = build_trace(records, request_id)
    if not steps:
        raise NotFoundError(
            f"no log lines carry request id {request_id}",
            details={"request_id": request_id},
        )

    out = [
        TraceStepOut(**_to_record(step.record).model_dump(), gap_ms=step.gap_ms) for step in steps
    ]
    first, last = steps[0].timestamp, steps[-1].timestamp
    span_ms = None
    if first is not None and last is not None:
        span_ms = round((last - first).total_seconds() * 1000, 2)

    return TraceOut(
        request_id=request_id,
        steps=out,
        # Ordered by first appearance, so the list reads as the path the request took.
        services=list(dict.fromkeys(step.service for step in out)),
        span_ms=span_ms,
        order_ids=sorted({step.order_id for step in out if step.order_id}),
    )


def errors(settings: Settings, since: str) -> ErrorsOut:
    records, _ = _load(settings)
    cutoff = _cutoff(since)
    groups = group_errors(filter_since(records, cutoff))
    return ErrorsOut(
        since=since,
        cutoff=cutoff,
        total=sum(group.count for group in groups),
        groups=[_to_group(group) for group in groups],
    )


def slow(settings: Settings, since: str, top: int) -> SlowOut:
    records, _ = _load(settings)
    cutoff = _cutoff(since)
    return SlowOut(
        since=since,
        cutoff=cutoff,
        records=[_to_record(record) for record in rank_slow(filter_since(records, cutoff), top)],
    )


def order_story(settings: Settings, order_id: str) -> OrderStoryOut:
    """Every line that mentions an order id anywhere, across every service."""
    records, _ = _load(settings)
    found = find_order(records, order_id)
    if not found:
        raise NotFoundError(
            f"no log lines mention order {order_id}", details={"order_id": order_id}
        )
    out = [_to_record(record) for record in found]
    return OrderStoryOut(
        order_id=order_id,
        records=out,
        request_ids=list(dict.fromkeys(r.request_id for r in out if r.request_id)),
    )


# --------------------------------------------------------------------------- health


def probe(settings: Settings) -> OverviewOut:
    """Ask every service how it is, and count recent errors, in one call.

    The console polls this, so it has to answer even when half the platform is down -
    a probe failure is a result, not an exception.
    """
    targets = list(settings.services.items())
    # In parallel, not in a loop. Serially, a console refresh costs the sum of three
    # /ready calls, and the moment one service is wedged that sum becomes the probe
    # timeout - so the screen you look at to find out what is down would itself be the
    # slowest thing on the screen.
    with (
        httpx.Client(timeout=settings.probe_timeout_s) as client,
        ThreadPoolExecutor(max_workers=len(targets) or 1) as pool,
    ):
        results = list(pool.map(lambda item: _probe_one(client, *item), targets))

    records, malformed = _load(settings)
    recent = filter_since(records, _cutoff(ERROR_WINDOW))
    return OverviewOut(
        generated_at=datetime.now(UTC),
        services=results,
        errors_last_15m=sum(group.count for group in group_errors(recent)),
        log_lines_read=len(records),
        malformed_lines=malformed,
    )


def _probe_one(client: httpx.Client, name: str, url: str) -> ServiceHealth:
    started = time.perf_counter()
    try:
        response = client.get(f"{url}/ready")
    except httpx.RequestError as exc:
        return ServiceHealth(name=name, url=url, live=False, ready=False, detail=type(exc).__name__)

    elapsed = round((time.perf_counter() - started) * 1000, 2)
    body = _json_or_none(response)
    checks: dict[str, str] = {}
    detail: str | None = None
    if isinstance(body, dict):
        # /ready answers with its checks on the way up and with the standard error
        # envelope on the way down; both carry the per-dependency detail.
        checks = body.get("checks") or (body.get("error", {}).get("details", {}) or {}).get(
            "checks", {}
        )
        detail = (body.get("error") or {}).get("message")

    return ServiceHealth(
        name=name,
        url=url,
        # A service that answers /ready at all, even with a 503, is running.
        live=True,
        ready=response.is_success,
        detail=detail,
        latency_ms=elapsed,
        checks=checks if isinstance(checks, dict) else {},
    )


# --------------------------------------------------------------------------- incidents


def incidents(settings: Settings) -> IncidentsOut:
    """The incident board: the public half of each fault, plus any closed-out row.

    It reads the same two files the chaos CLI does, and deliberately never touches the
    `spoiler` field - the console is something you look at *during* an investigation.
    """
    base = settings.incidents_path
    scored = _index_rows(base / "INDEX.md")
    out: list[IncidentOut] = []

    for path in sorted((base / "faults").glob("INC-*.json")):
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            log.warning("fault_file_unreadable", path=str(path))
            continue
        row = scored.get(raw["id"], {})
        out.append(
            IncidentOut(
                id=raw["id"],
                title=raw["title"],
                category=raw["category"],
                difficulty=raw["difficulty"],
                status="closed" if row else _status_on_disk(base / raw["id"]),
                severity=row.get("sev"),
                tta_min=_as_int(row.get("tta")),
                ttm_min=_as_int(row.get("ttm")),
                hints=_as_int(row.get("hints")),
                rca_score=row.get("score"),
            )
        )

    return IncidentsOut(
        incidents=out,
        closed=sum(1 for item in out if item.status == "closed"),
        total=len(out),
    )


def _status_on_disk(folder: Path) -> str:
    if not folder.is_dir():
        return "not started"
    return "in progress" if (folder / "investigation.md").exists() else "opened"


def _index_rows(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    rows: dict[str, dict[str, str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("| INC-"):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(cells) < 8:
            continue
        rows[cells[0]] = {
            "sev": cells[2],
            "tta": cells[4],
            "ttm": cells[5],
            "hints": cells[6],
            "score": cells[7],
        }
    return rows


# --------------------------------------------------------------------------- support


def _cutoff(since: str) -> datetime:
    try:
        window = parse_duration(since)
    except ValueError as exc:
        raise ValidationFailedError(str(exc), details={"since": since}) from exc
    return datetime.now(UTC) - window


def _to_group(group: ErrorGroup) -> ErrorGroupOut:
    return ErrorGroupOut(
        service=group.service,
        error_code=group.error_code,
        count=group.count,
        first_seen=group.first_seen,
        last_seen=group.last_seen,
        example=_to_record(group.example),
    )


def _json_or_none(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def _as_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _as_float(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def request_ids_in(records: Iterable[LogRecord]) -> list[str]:
    return list(dict.fromkeys(record.request_id for record in records if record.request_id))
