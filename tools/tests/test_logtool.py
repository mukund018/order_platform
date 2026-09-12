from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import logtool
import pytest

BASE = datetime(2026, 9, 11, 10, 0, 0, tzinfo=UTC)


def at(offset_s: float) -> str:
    return (BASE + timedelta(seconds=offset_s)).isoformat().replace("+00:00", "Z")


def record(offset_s: float, **fields: Any) -> dict[str, Any]:
    line: dict[str, Any] = {
        "timestamp": at(offset_s),
        "level": "info",
        "service": "orders",
        "event": "http_request",
    }
    line.update(fields)
    return line


def write_log(
    path: Path, records: list[dict[str, Any]], extra_lines: list[str] | None = None
) -> None:
    lines = [json.dumps(item) for item in records] + list(extra_lines or [])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("30s", timedelta(seconds=30)),
        ("15m", timedelta(minutes=15)),
        ("2h", timedelta(hours=2)),
        ("1d", timedelta(days=1)),
        (" 45M ", timedelta(minutes=45)),
    ],
)
def test_parse_duration_units(text: str, expected: timedelta) -> None:
    assert logtool.parse_duration(text) == expected


@pytest.mark.parametrize("text", ["", "15", "m", "abc", "-5m", "1.5h", "15x", "0m"])
def test_parse_duration_rejects_garbage(text: str) -> None:
    with pytest.raises(ValueError):
        logtool.parse_duration(text)


def test_parse_line_skips_unusable_lines() -> None:
    assert logtool.parse_line('{"event": "ok"}') == {"event": "ok"}
    assert logtool.parse_line('{"event": "half-writ') is None
    assert logtool.parse_line("   ") is None
    assert logtool.parse_line("[1, 2, 3]") is None


def test_parse_timestamp_handles_z_and_naive() -> None:
    assert logtool.parse_timestamp("2026-09-11T10:00:00Z") == BASE
    assert logtool.parse_timestamp("2026-09-11T10:00:00") == BASE
    assert logtool.parse_timestamp("not a time") is None
    assert logtool.parse_timestamp(None) is None


def test_read_records_counts_malformed_and_fills_service(tmp_path: Path) -> None:
    write_log(
        tmp_path / "orders.log",
        [record(0, event="order_created"), record(1, event="http_request")],
        extra_lines=['{"event": "truncated', ""],
    )
    write_log(tmp_path / "inventory.log", [{"timestamp": at(2), "level": "info", "event": "hit"}])

    records, malformed = logtool.read_records(tmp_path)

    assert len(records) == 3
    assert malformed == 1
    # The line without a service key is attributed from the file it came out of.
    assert {item["service"] for item in records} == {"orders", "inventory"}


def test_read_records_on_empty_dir(tmp_path: Path) -> None:
    assert logtool.read_records(tmp_path) == ([], 0)


def test_tail_bytes_none_reads_the_whole_file_as_before(tmp_path: Path) -> None:
    """The CLI's own default. A person running logtool.py by hand is explicitly asking
    for the full history and can decide whether to wait for it."""
    write_log(tmp_path / "orders.log", [record(i, event="http_request") for i in range(50)])

    records, _ = logtool.read_records(tmp_path, tail_bytes=None)

    assert len(records) == 50


def test_tail_bytes_bounds_a_large_file_to_its_last_lines(tmp_path: Path) -> None:
    """support-service's own default, discovered live: reading cost has to stay flat
    as a file grows, or /overview goes from instant to tens of seconds."""
    write_log(tmp_path / "orders.log", [record(i, event="http_request", n=i) for i in range(200)])
    file_size = (tmp_path / "orders.log").stat().st_size

    records, malformed = logtool.read_records(tmp_path, tail_bytes=file_size // 4)

    assert 0 < len(records) < 200
    # The oldest lines are the ones dropped, not the newest.
    assert records[-1]["n"] == 199
    assert malformed == 0, "the partial line the seek lands inside must be discarded, not counted"


def test_tail_bytes_larger_than_the_file_reads_everything(tmp_path: Path) -> None:
    write_log(tmp_path / "orders.log", [record(i, event="http_request") for i in range(10)])

    records, _ = logtool.read_records(tmp_path, tail_bytes=10_000_000)

    assert len(records) == 10


def test_filter_since_keeps_only_the_window() -> None:
    now = datetime.now(UTC)
    records = [
        {"timestamp": (now - timedelta(minutes=30)).isoformat(), "event": "old"},
        {"timestamp": (now - timedelta(minutes=5)).isoformat(), "event": "recent"},
        {"timestamp": "garbage", "event": "undated"},
    ]

    kept = logtool.filter_since(records, now - timedelta(minutes=15))

    assert [item["event"] for item in kept] == ["recent"]


def test_build_trace_orders_lines_and_measures_gaps() -> None:
    records = [
        record(0.2, service="payments", request_id="abc"),
        record(0, service="orders", request_id="abc"),
        record(0.076, service="inventory", request_id="abc"),
        record(0.1, service="orders", request_id="other"),
    ]

    steps = logtool.build_trace(records, "abc")

    assert [step.record["service"] for step in steps] == ["orders", "inventory", "payments"]
    assert [step.gap_ms for step in steps] == [None, 76.0, 124.0]


def test_build_trace_without_matches() -> None:
    assert logtool.build_trace([record(0, request_id="abc")], "zzz") == []


def test_group_errors_counts_sorts_and_keeps_latest_example() -> None:
    records = [
        record(0, level="error", service="payments", error_code="UPSTREAM_TIMEOUT", attempt=1),
        record(1, level="error", service="payments", error_code="UPSTREAM_TIMEOUT", attempt=2),
        record(2, level="warning", service="orders", error_code="OUT_OF_STOCK"),
        record(3, level="info", service="orders", error_code="OUT_OF_STOCK"),
        record(4, level="error", service="inventory", event="cache_miss_storm"),
    ]

    groups = logtool.group_errors(records)

    assert [(g.service, g.error_code, g.count) for g in groups] == [
        ("payments", "UPSTREAM_TIMEOUT", 2),
        ("inventory", "cache_miss_storm", 1),
        ("orders", "OUT_OF_STOCK", 1),
    ]
    assert groups[0].example["attempt"] == 2
    assert groups[0].first_seen == BASE
    assert groups[0].last_seen == BASE + timedelta(seconds=1)


def test_group_errors_ignores_info_only_logs() -> None:
    assert logtool.group_errors([record(0), record(1)]) == []


def test_rank_slow_takes_the_worst_http_requests() -> None:
    records = [
        record(0, duration_ms=12.0, path="/products"),
        record(1, duration_ms=1840.5, path="/orders"),
        record(2, duration_ms=300.0, path="/orders/{order_id}"),
        record(3, event="order_created", duration_ms=99999.0),
        record(4, path="/health"),
    ]

    slowest = logtool.rank_slow(records, top=2)

    assert [item["path"] for item in slowest] == ["/orders", "/orders/{order_id}"]


def test_find_order_matches_nested_values_case_insensitively() -> None:
    order_id = "2B8A1F0C-1111-4A00-9E00-000000000001"
    records = [
        record(1, event="order_created", order_id=order_id.lower()),
        record(0, event="reservation_created", details={"order": order_id.lower()}),
        record(2, event="payment_captured", order_id="00000000-0000-0000-0000-000000000009"),
        record(3, event="note", message=f"released reservation for {order_id.lower()}"),
    ]

    found = logtool.find_order(records, order_id)

    assert [item["event"] for item in found] == ["reservation_created", "order_created", "note"]


def test_format_record_includes_context_keys() -> None:
    line = logtool.format_record(
        record(0, request_id="abc", status_code=500, duration_ms=12.5), gap_ms=76.0
    )

    assert "+76ms" in line
    assert "status_code=500" in line
    assert "duration_ms=12.5" in line
    assert "abc" not in line
