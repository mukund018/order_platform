"""Reading and analysing the platform's JSON logs.

This is the engine behind `tools/logtool.py`, and it lives in `common` for the same
reason the logging configuration does: there is now a second front end for it - the
support-service HTTP API that the ops console calls - and two copies of "what counts as
an error line" would drift within a week.

Everything here is a pure function over dicts. No argparse, no printing, no FastAPI:
that all belongs to whichever front end is asking.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ACCESS_EVENT = "http_request"
# Group key for an error line whose "event" is a sentence from a third-party library
# rather than one of our own event names.
UNSTRUCTURED = "UNSTRUCTURED"
ERROR_LEVELS = frozenset({"warning", "error", "critical"})
STANDARD_KEYS = frozenset({"timestamp", "level", "service", "event", "request_id", "logger"})
DURATION_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}

# Sort key for a line whose timestamp we could not read: it goes first rather than
# blowing up the comparison.
EPOCH = datetime.min.replace(tzinfo=UTC)


@dataclass(frozen=True)
class TraceStep:
    record: dict[str, Any]
    timestamp: datetime | None
    gap_ms: float | None


@dataclass(frozen=True)
class ErrorGroup:
    service: str
    error_code: str
    count: int
    first_seen: datetime | None
    last_seen: datetime | None
    example: dict[str, Any]


def parse_duration(text: str) -> timedelta:
    """'30s', '15m', '2h', '1d' -> timedelta. Anything else is a ValueError."""
    raw = text.strip().lower()
    unit = raw[-1:] if raw else ""
    if unit not in DURATION_UNITS or not raw[:-1].isdigit():
        raise ValueError(f"bad duration {text!r}: expected something like 30s, 15m, 2h, 1d")
    amount = int(raw[:-1])
    if amount == 0:
        raise ValueError(f"bad duration {text!r}: must be greater than zero")
    return timedelta(**{DURATION_UNITS[unit]: amount})


def parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def parse_line(line: str) -> dict[str, Any] | None:
    """None for a blank or half-written line: tailing a live file gives you those."""
    text = line.strip()
    if not text:
        return None
    try:
        record = json.loads(text)
    except ValueError:
        return None
    return record if isinstance(record, dict) else None


def read_records(
    log_dir: Path, pattern: str = "*.log", tail_bytes: int | None = None
) -> tuple[list[dict[str, Any]], int]:
    """All records from the log directory, plus a count of lines that would not parse.

    `tail_bytes`, when given, bounds the read to the last N bytes of each file instead
    of the whole thing, so cost stays flat as a file grows instead of scaling with its
    entire history - discovered live, the hard way: after a day's worth of traffic and
    incident testing, four log files totalling ~280k lines took support-service's
    `/overview` from instant to 15-50+ seconds, entirely inside this function, with
    every dependency it calls answering in under half a second. The line straddling
    the seek point is a partial line and is discarded, not counted as malformed.

    `None` (the default) reads the whole file - `tools/logtool.py`'s CLI keeps this
    default, because a person running it by hand is explicitly asking for the full
    history and can decide whether to wait for it. support-service's HTTP API, which
    nothing is waiting for a human to tolerate, does not get that default; see its
    own `SUPPORT_LOG_TAIL_BYTES` setting.
    """
    records: list[dict[str, Any]] = []
    malformed = 0
    for path in sorted(log_dir.glob(pattern)):
        size = path.stat().st_size
        with path.open("rb") as handle:
            if tail_bytes is not None and size > tail_bytes:
                handle.seek(size - tail_bytes)
                handle.readline()  # discard the partial line the seek landed inside
            for raw_line in handle:
                line = raw_line.decode("utf-8", errors="replace")
                record = parse_line(line)
                if record is None:
                    if line.strip():
                        malformed += 1
                    continue
                record.setdefault("service", path.stem)
                records.append(record)
    return records, malformed


def sort_records(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(records, key=lambda record: parse_timestamp(record.get("timestamp")) or EPOCH)


def filter_since(records: Iterable[dict[str, Any]], cutoff: datetime) -> list[dict[str, Any]]:
    """A line we cannot place in time is dropped rather than assumed to be recent."""
    kept = []
    for record in records:
        timestamp = parse_timestamp(record.get("timestamp"))
        if timestamp is not None and timestamp >= cutoff:
            kept.append(record)
    return kept


def error_code_of(record: dict[str, Any]) -> str:
    """The label an error line is grouped under.

    Our own events are short snake_case names, so they make good group keys. Lines from
    libraries we do not control - celery's broker retries, for one - put a whole English
    sentence in `event`, and those sentences differ by a timestamp or a retry delay, so
    using them as keys gives one group per line and a useless dashboard. Anything with a
    space in it is prose, not an event name, and is counted as one bucket instead.
    """
    for key in ("error_code", "code"):
        value = record.get(key)
        if isinstance(value, str) and value:
            return value
    event = record.get("event")
    if not event:
        return "UNKNOWN"
    text = str(event)
    return text if " " not in text.strip() else UNSTRUCTURED


def mentions(value: Any, needle: str) -> bool:
    if isinstance(value, dict):
        return any(mentions(item, needle) for item in value.values())
    if isinstance(value, list | tuple):
        return any(mentions(item, needle) for item in value)
    if isinstance(value, str):
        return needle in value.lower()
    return False


def build_trace(records: Iterable[dict[str, Any]], request_id: str) -> list[TraceStep]:
    """Every line for one request, in time order, with the gap since the previous line."""
    matching = [record for record in records if record.get("request_id") == request_id]
    steps: list[TraceStep] = []
    previous: datetime | None = None
    for record in sort_records(matching):
        timestamp = parse_timestamp(record.get("timestamp"))
        gap_ms = None
        if timestamp is not None and previous is not None:
            gap_ms = round((timestamp - previous).total_seconds() * 1000, 2)
        steps.append(TraceStep(record=record, timestamp=timestamp, gap_ms=gap_ms))
        if timestamp is not None:
            previous = timestamp
    return steps


def group_errors(records: Iterable[dict[str, Any]]) -> list[ErrorGroup]:
    buckets: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        level = str(record.get("level", "")).lower()
        if level not in ERROR_LEVELS:
            continue
        service = str(record.get("service") or "unknown")
        buckets[(service, error_code_of(record))].append(record)

    groups = []
    for (service, code), found in buckets.items():
        ordered = sort_records(found)
        groups.append(
            ErrorGroup(
                service=service,
                error_code=code,
                count=len(ordered),
                first_seen=parse_timestamp(ordered[0].get("timestamp")),
                last_seen=parse_timestamp(ordered[-1].get("timestamp")),
                example=ordered[-1],
            )
        )
    return sorted(groups, key=lambda group: (-group.count, group.service, group.error_code))


def rank_slow(
    records: Iterable[dict[str, Any]], top: int = 10, event: str = ACCESS_EVENT
) -> list[dict[str, Any]]:
    candidates = [
        record
        for record in records
        if record.get("event") == event and isinstance(record.get("duration_ms"), int | float)
    ]
    candidates.sort(key=lambda record: float(record["duration_ms"]), reverse=True)
    return candidates[:top]


def find_order(records: Iterable[dict[str, Any]], order_id: str) -> list[dict[str, Any]]:
    needle = order_id.strip().lower()
    return sort_records(record for record in records if mentions(record, needle))
