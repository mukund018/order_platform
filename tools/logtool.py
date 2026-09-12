"""Answer the four questions that come up during an incident, from the JSON logs.

    python tools/logtool.py trace 3f2b9c1d4e...
    python tools/logtool.py errors --since 15m
    python tools/logtool.py slow --top 10 --since 1h
    python tools/logtool.py order 2b8a...

Every service writes newline-delimited JSON to logs/<service>.log. The reading,
grouping and ranking all live in `common.logsearch`, because support-service serves the
same four answers over HTTP to the ops console and one of them drifting from the other
would make the console lie. What stays here is the command line and the formatting.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from common.logsearch import (
    ACCESS_EVENT,
    STANDARD_KEYS,
    UNSTRUCTURED,
    ErrorGroup,
    TraceStep,
    build_trace,
    error_code_of,
    filter_since,
    find_order,
    group_errors,
    parse_duration,
    parse_line,
    parse_timestamp,
    rank_slow,
    read_records,
    sort_records,
)

__all__ = [
    "ACCESS_EVENT",
    "STANDARD_KEYS",
    "UNSTRUCTURED",
    "ErrorGroup",
    "TraceStep",
    "build_trace",
    "error_code_of",
    "filter_since",
    "find_order",
    "group_errors",
    "main",
    "parse_duration",
    "parse_line",
    "parse_timestamp",
    "rank_slow",
    "read_records",
    "sort_records",
]

DEFAULT_LOG_DIR = Path(__file__).resolve().parents[1] / "logs"
MAX_VALUE_CHARS = 120


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
