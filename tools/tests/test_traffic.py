from __future__ import annotations

import random

import pytest
import traffic
from traffic import BUSINESS, CLIENT, OK, SERVER, TRANSPORT, Result


def test_percentile_on_a_known_series() -> None:
    values = [float(n) for n in range(1, 11)]

    assert traffic.percentile(values, 0) == 1.0
    assert traffic.percentile(values, 50) == 5.5
    assert traffic.percentile(values, 100) == 10.0
    assert traffic.percentile(values, 95) == pytest.approx(9.55)


def test_percentile_edge_cases() -> None:
    assert traffic.percentile([], 95) == 0.0
    assert traffic.percentile([42.0], 99) == 42.0


def test_percentile_ignores_input_order() -> None:
    assert traffic.percentile([9.0, 1.0, 5.0], 50) == 5.0


@pytest.mark.parametrize(
    ("status", "order_status", "expected"),
    [
        (None, None, TRANSPORT),
        (500, None, SERVER),
        (503, None, SERVER),
        (409, None, CLIENT),
        (422, None, CLIENT),
        (200, None, OK),
        (201, "CONFIRMED", OK),
        (201, "PENDING", OK),
        (201, "RESERVED", OK),
        (201, "FAILED", BUSINESS),
        (201, "expired", BUSINESS),
        (200, "CANCELLED", BUSINESS),
    ],
)
def test_classify(status: int | None, order_status: str | None, expected: str) -> None:
    assert traffic.classify(status, order_status) == expected


@pytest.mark.parametrize(
    "payload",
    [
        [{"sku": "SKU-0001"}, {"sku": "SKU-0002"}],
        {"items": [{"sku": "SKU-0001"}, {"sku": "SKU-0002"}]},
        {"products": [{"sku": "SKU-0001"}, {"sku": "SKU-0002"}]},
    ],
)
def test_extract_skus_accepts_the_usual_shapes(payload: object) -> None:
    assert traffic.extract_skus(payload) == ["SKU-0001", "SKU-0002"]


def test_extract_skus_skips_junk() -> None:
    payload = [{"sku": "SKU-0001"}, {"name": "no sku"}, "string", {"sku": 7}]

    assert traffic.extract_skus(payload) == ["SKU-0001"]
    assert traffic.extract_skus({"error": {"code": "NOT_FOUND"}}) == []
    assert traffic.extract_skus(None) == []


def test_choose_skus_returns_distinct_skus_from_the_catalogue() -> None:
    catalogue = [f"SKU-{n:04d}" for n in range(1, 21)]
    rng = random.Random(11)

    for _ in range(200):
        chosen = traffic.choose_skus(catalogue, None, rng)
        assert 1 <= len(chosen) <= traffic.MAX_ITEMS_PER_ORDER
        assert len(set(chosen)) == len(chosen)
        assert set(chosen) <= set(catalogue)


def test_choose_skus_favours_the_hot_sku() -> None:
    catalogue = [f"SKU-{n:04d}" for n in range(1, 21)]
    rng = random.Random(5)

    orders = [traffic.choose_skus(catalogue, "SKU-0007", rng) for _ in range(500)]
    share = sum("SKU-0007" in order for order in orders) / len(orders)

    # 50% forced plus whatever the random picks add; anything near 5% means the
    # hot sku is not actually hot.
    assert share > 0.4


def test_choose_skus_with_empty_catalogue() -> None:
    assert traffic.choose_skus([], None, random.Random(1)) == []


def test_summarise_separates_business_failures_from_errors() -> None:
    results = [
        Result("browse_list", OK, 10.0, 200),
        Result("browse_sku", OK, 20.0, 200),
        Result("order", OK, 30.0, 201),
        Result("order", BUSINESS, 40.0, 201, "out of stock"),
        Result("order", SERVER, 50.0, 500, "INTERNAL_ERROR", "req-1"),
        Result("browse_sku", TRANSPORT, 60.0, None, "ReadTimeout"),
    ]

    summary = traffic.summarise(results, elapsed_s=60.0)

    assert summary["requests"] == 6
    assert summary["orders"] == 3
    assert summary["by_outcome"][OK] == 3
    assert summary["success_rate_pct"] == 60.0
    assert summary["business_failure_pct"] == pytest.approx(33.33)
    assert summary["business_reasons"] == {"out of stock": 1}
    assert summary["failures_by_status"] == {"500": 1, "transport": 1}
    assert summary["failures_by_code"] == {"INTERNAL_ERROR": 1, "ReadTimeout": 1}
    assert summary["examples"] == {"INTERNAL_ERROR": "req-1"}
    assert summary["latency_ms"]["p50"] == 35.0
    assert summary["achieved_rps"] == 0.1


def test_summarise_with_no_results() -> None:
    summary = traffic.summarise([], elapsed_s=5.0)

    assert summary["requests"] == 0
    assert summary["success_rate_pct"] == 0.0
    assert summary["latency_ms"] == {"p50": 0.0, "p95": 0.0, "p99": 0.0}


def test_format_summary_mentions_every_outcome_bucket() -> None:
    summary = traffic.summarise(
        [Result("order", BUSINESS, 12.0, 201, "payment declined")], elapsed_s=1.0
    )

    text = "\n".join(traffic.format_summary(summary))

    assert "business failures" in text
    assert "payment declined" in text
