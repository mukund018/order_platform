"""Answer the four questions that come up during an incident, from the JSON logs.

    python tools/logtool.py trace 3f2b9c1d4e...
    python tools/logtool.py errors --since 15m
    python tools/logtool.py slow --top 10 --since 1h
    python tools/logtool.py order 2b8a...

Every service writes newline-delimited JSON to logs/<service>.log. Reading, grouping
and ranking are plain functions over dicts; argparse and printing stay at the edges so
the interesting parts can be tested without a log directory.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

DEFAULT_LOG_DIR = Path(__file__).resolve().parents[1] / "logs"
ACCESS_EVENT = "http_request"
ERROR_LEVELS = frozenset({"warning", "error", "critical"})
STANDARD_KEYS = frozenset({"timestamp", "level", "service", "event", "request_id", "logger"})
DURATION_UNITS = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}
EPOCH = datetime.min.replace(tzinfo=UTC)
MAX_VALUE_CHARS = 120


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


def read_records(log_dir: Path, pattern: str = "*.log") -> tuple[list[dict[str, Any]], int]:
    """All records from the log directory, plus a count of lines that would not parse."""
    records: list[dict[str, Any]] = []
    malformed = 0
    for path in sorted(log_dir.glob(pattern)):
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
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
    for key in ("error_code", "code"):
        value = record.get(key)
        if isinstance(value, str) and value:
            return value
    event = record.get("event")
    return str(event) if event else "UNKNOWN"


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


def format_time(timestamp: datetime | None) -> str:
    if timestamp is None:
        return "--:--:--.---"
    return timestamp.astimezone(UTC).strftime("%H:%M:%S.%f")[:-3]


def format_extras(record: dict[str, Any]) -> str:
    parts = []
    for key, value in record.items():
        if key in STANDARD_KEYS:
            continue
        text = value if isinstance(value, str) else json.dumps(value, default=str)
        if len(text) > MAX_VALUE_CHARS:
            text = text[:MAX_VALUE_CHARS] + "..."
        parts.append(f"{key}={text}")
    return " ".join(parts)


def format_record(record: dict[str, Any], gap_ms: float | None = None) -> str:
    timestamp = parse_timestamp(record.get("timestamp"))
    gap = "" if gap_ms is None else f"+{gap_ms:.0f}ms"
    return (
        f"{format_time(timestamp)}  {gap:>9}  {record.get('service', '-')!s:<10} "
        f"{record.get('level', '-')!s:<8} {record.get('event', '-')!s:<24} "
        f"{format_extras(record)}".rstrip()
    )


def dump_json(payload: Any) -> None:
    print(json.dumps(payload, default=str, indent=2))


def cmd_trace(records: list[dict[str, Any]], malformed: int, args: argparse.Namespace) -> int:
    steps = build_trace(records, args.request_id)
    if args.as_json:
        dump_json(
            {
                "request_id": args.request_id,
                "malformed_lines": malformed,
                "steps": [{"gap_ms": step.gap_ms, **step.record} for step in steps],
            }
        )
        return 0 if steps else 1
    if not steps:
        print(f"no lines for request_id {args.request_id}")
        return 1

    services = {step.record.get("service") for step in steps}
    span = _span_ms(steps[0].timestamp, steps[-1].timestamp)
    date = steps[0].timestamp.date().isoformat() if steps[0].timestamp else "unknown date"
    print(
        f"request_id {args.request_id}  {len(steps)} lines across {len(services)} services  "
        f"{span}  ({date}, times UTC)"
    )
    for step in steps:
        print(format_record(step.record, step.gap_ms))
    report_malformed(malformed)
    return 0


def cmd_errors(records: list[dict[str, Any]], malformed: int, args: argparse.Namespace) -> int:
    groups = group_errors(records)
    if args.as_json:
        dump_json(
            {
                "malformed_lines": malformed,
                "groups": [
                    {
                        "service": group.service,
                        "error_code": group.error_code,
                        "count": group.count,
                        "first_seen": _iso(group.first_seen),
                        "last_seen": _iso(group.last_seen),
                        "example": group.example,
                    }
                    for group in groups
                ],
            }
        )
        return 0
    if not groups:
        print("no errors or warnings in the window")
        return 0

    print(f"{'COUNT':>6}  {'SERVICE':<12} {'CODE':<26} {'LAST SEEN':<13} EXAMPLE")
    for group in groups:
        example = format_extras(group.example) or str(group.example.get("event", ""))
        if len(example) > 90:
            example = example[:90] + "..."
        print(
            f"{group.count:>6}  {group.service:<12} {group.error_code:<26} "
            f"{format_time(group.last_seen):<13} {example}"
        )
    report_malformed(malformed)
    return 0


def cmd_slow(records: list[dict[str, Any]], malformed: int, args: argparse.Namespace) -> int:
    slowest = rank_slow(records, args.top)
    if args.as_json:
        dump_json({"malformed_lines": malformed, "requests": slowest})
        return 0
    if not slowest:
        print("no http_request lines in the window")
        return 0

    print(f"{'DURATION':>10}  {'SERVICE':<12} {'STATUS':<7} {'METHOD':<7} {'PATH':<32} REQUEST ID")
    for record in slowest:
        print(
            f"{float(record['duration_ms']):>8.1f}ms  {record.get('service', '-')!s:<12} "
            f"{record.get('status_code', '-')!s:<7} {record.get('method', '-')!s:<7} "
            f"{record.get('path', '-')!s:<32} {record.get('request_id', '-')}"
        )
    report_malformed(malformed)
    return 0


def cmd_order(records: list[dict[str, Any]], malformed: int, args: argparse.Namespace) -> int:
    found = find_order(records, args.order_id)
    if args.as_json:
        dump_json({"order_id": args.order_id, "malformed_lines": malformed, "lines": found})
        return 0 if found else 1
    if not found:
        print(f"no lines mentioning order {args.order_id}")
        return 1

    request_ids = {record.get("request_id") for record in found if record.get("request_id")}
    print(f"order {args.order_id}  {len(found)} lines  {len(request_ids)} request ids")
    for record in found:
        print(format_record(record))
    report_malformed(malformed)
    return 0


def report_malformed(malformed: int) -> None:
    if malformed:
        plural = "" if malformed == 1 else "s"
        print(f"({malformed} unparseable line{plural} skipped)", file=sys.stderr)


def _iso(timestamp: datetime | None) -> str | None:
    return timestamp.isoformat() if timestamp else None


def _span_ms(first: datetime | None, last: datetime | None) -> str:
    if first is None or last is None:
        return "span unknown"
    return f"span {(last - first).total_seconds() * 1000:.0f}ms"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="logtool", description="search the platform JSON logs")
    parser.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR)
    subcommands = parser.add_subparsers(dest="command", required=True)

    trace = subcommands.add_parser("trace", help="every line for one request id")
    trace.add_argument("request_id")
    trace.set_defaults(handler=cmd_trace)

    errors = subcommands.add_parser("errors", help="errors and warnings grouped by code")
    errors.add_argument("--since", default=None, metavar="15m")
    errors.set_defaults(handler=cmd_errors)

    slow = subcommands.add_parser("slow", help="slowest requests")
    slow.add_argument("--top", type=int, default=10)
    slow.add_argument("--since", default=None, metavar="15m")
    slow.set_defaults(handler=cmd_slow)

    order = subcommands.add_parser("order", help="every line mentioning one order id")
    order.add_argument("order_id")
    order.set_defaults(handler=cmd_order)

    for subcommand in (trace, errors, slow, order):
        subcommand.add_argument("--json", action="store_true", dest="as_json")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    log_dir: Path = args.log_dir
    if not log_dir.is_dir():
        print(f"no log directory at {log_dir}", file=sys.stderr)
        return 2

    records, malformed = read_records(log_dir)
    if not records and malformed == 0:
        print(f"no log lines under {log_dir}", file=sys.stderr)
        return 1

    since = getattr(args, "since", None)
    if since:
        try:
            window = parse_duration(since)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        records = filter_since(records, datetime.now(UTC) - window)

    return int(args.handler(records, malformed, args))


if __name__ == "__main__":
    raise SystemExit(main())
