import re
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

import pytest

from app.config import Settings
from app.gateway import DECLINE_REASONS, Gateway

PROVIDER_REF = re.compile(r"^PAY-[0-9a-f]{12}$")


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "PAYMENTS_DATABASE_URL": "sqlite://",
        "GATEWAY_LATENCY_MS_MIN": 0,
        "GATEWAY_LATENCY_MS_MAX": 0,
        "GATEWAY_FAILURE_RATE": 0.0,
        "GATEWAY_TIMEOUT_RATE": 0.0,
        "GATEWAY_TIMEOUT_SLEEP_S": 0.3,
        "GATEWAY_SEED": 7,
    }
    values.update(overrides)
    return Settings(**values)


def test_approves_and_returns_a_provider_ref() -> None:
    result = Gateway(make_settings()).charge(uuid.uuid4(), 12_500)

    assert result.approved is True
    assert PROVIDER_REF.match(result.provider_ref or "")
    assert result.failure_reason is None


def test_declines_with_a_realistic_reason() -> None:
    result = Gateway(make_settings(GATEWAY_FAILURE_RATE=1.0)).charge(uuid.uuid4(), 12_500)

    assert result.approved is False
    assert result.provider_ref is None
    assert result.failure_reason in DECLINE_REASONS


def test_same_seed_gives_the_same_sequence() -> None:
    def run() -> list[tuple[bool, str | None]]:
        gateway = Gateway(make_settings(GATEWAY_FAILURE_RATE=0.5, GATEWAY_SEED=42))
        results = [gateway.charge(uuid.uuid4(), 100) for _ in range(20)]
        return [(r.approved, r.failure_reason) for r in results]

    assert run() == run()


def test_unseeded_gateways_diverge() -> None:
    def run() -> list[bool]:
        gateway = Gateway(make_settings(GATEWAY_FAILURE_RATE=0.5, GATEWAY_SEED=None))
        return [gateway.charge(uuid.uuid4(), 100).approved for _ in range(40)]

    assert run() != run()


def test_latency_stays_inside_the_configured_window() -> None:
    gateway = Gateway(make_settings(GATEWAY_LATENCY_MS_MIN=20, GATEWAY_LATENCY_MS_MAX=40))

    started = time.perf_counter()
    result = gateway.charge(uuid.uuid4(), 100)
    elapsed_ms = (time.perf_counter() - started) * 1000

    assert 20 <= result.latency_ms <= 40
    assert elapsed_ms >= 15  # clock granularity on Windows is coarse


def test_timeout_mode_outlasts_a_short_caller_timeout() -> None:
    gateway = Gateway(make_settings(GATEWAY_TIMEOUT_RATE=1.0, GATEWAY_TIMEOUT_SLEEP_S=0.3))

    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(gateway.charge, uuid.uuid4(), 100)
        with pytest.raises(FutureTimeout):
            pending.result(timeout=0.05)

        # The caller gave up, but the charge still goes through - which is exactly the
        # inconsistency orders-service has to cope with.
        result = pending.result(timeout=5)

    assert result.approved is True
    assert result.latency_ms == 300


def test_rejects_impossible_probability_mix() -> None:
    with pytest.raises(ValueError, match=r"must be <= 1\.0"):
        make_settings(GATEWAY_TIMEOUT_RATE=0.7, GATEWAY_FAILURE_RATE=0.7)


def test_rejects_inverted_latency_bounds() -> None:
    with pytest.raises(ValueError, match="GATEWAY_LATENCY_MS_MAX"):
        make_settings(GATEWAY_LATENCY_MS_MIN=200, GATEWAY_LATENCY_MS_MAX=100)


def test_rejects_a_gateway_latency_ceiling_above_what_any_caller_would_wait() -> None:
    """INC-004: a gateway that can legitimately outlast orders-service's own
    PAYMENTS_TIMEOUT_S (3.0s default) guarantees some rate of "charged but recorded
    FAILED" - regardless of which service's timeout you look at, that combination can
    only produce mismatched charges. This must fail at startup, not in production."""
    with pytest.raises(ValueError, match="GATEWAY_LATENCY_MS_MAX"):
        make_settings(GATEWAY_LATENCY_MS_MIN=50, GATEWAY_LATENCY_MS_MAX=4200)
