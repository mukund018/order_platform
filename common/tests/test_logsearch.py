"""The parts of log analysis that decide what an operator sees.

The reading and ranking functions are covered through `tools/logtool.py`'s own suite.
What is tested here is the grouping rule, because it is the one piece of judgement in
the module: it decides whether an error board shows five useful rows or two hundred
useless ones.
"""

from __future__ import annotations

from typing import Any

from common.logsearch import UNSTRUCTURED, error_code_of, group_errors


def line(**fields: Any) -> dict[str, Any]:
    record = {"timestamp": "2026-09-12T10:00:00Z", "level": "error", "service": "orders"}
    record.update(fields)
    return record


def test_an_explicit_error_code_wins() -> None:
    assert error_code_of(line(error_code="OUT_OF_STOCK", event="reservation_rejected")) == (
        "OUT_OF_STOCK"
    )


def test_our_own_event_name_is_the_code_when_there_is_no_error_code() -> None:
    assert error_code_of(line(event="upstream_timeout")) == "upstream_timeout"


def test_prose_from_a_library_is_not_used_as_a_code() -> None:
    """celery writes a whole sentence into `event`, and the sentence carries a retry
    delay that differs every time. Using it as a key gives one group per line."""
    template = "beat: Connection error: Error -2 connecting to redis:6379. Trying again in {}s..."
    first = line(service="worker", event=template.format("10.0"))
    second = line(service="worker", event=template.format("12.0"))

    assert error_code_of(first) == UNSTRUCTURED
    groups = group_errors([first, second])

    assert len(groups) == 1
    assert groups[0].count == 2
    # The text is not thrown away - the example still carries it.
    assert "Connection error" in groups[0].example["event"]


def test_a_line_with_nothing_to_group_on() -> None:
    assert error_code_of(line()) == "UNKNOWN"


def test_grouping_splits_on_service_as_well_as_code() -> None:
    groups = group_errors(
        [
            line(service="orders", error_code="UPSTREAM_TIMEOUT"),
            line(service="orders", error_code="UPSTREAM_TIMEOUT"),
            line(service="inventory", error_code="UPSTREAM_TIMEOUT"),
        ]
    )

    assert [(group.service, group.count) for group in groups] == [("orders", 2), ("inventory", 1)]


def test_info_lines_are_not_errors() -> None:
    assert group_errors([line(level="info", event="order_created")]) == []
