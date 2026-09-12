"""Configuration that can only produce failures must not start.

Regression test for INC-001, where INVENTORY_TIMEOUT_S was 0.25 in a deployed
environment. inventory-service was healthy throughout - p95 33ms, zero 5xx - but a 250ms
budget bounds what the *client* observes, queueing included, so roughly 2% of checkouts
were abandoned mid-flight while inventory went on answering them successfully.

The lesson was not "2.0 is the right number". It was that nothing anywhere would have
stopped a number too small to survive, and the cheapest place to stop one is at startup.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import MIN_ORDER_EXPIRY_MINUTES, MIN_UPSTREAM_TIMEOUT_S, Settings

DATABASE_URL = "postgresql+psycopg://app:app@postgres:5432/orders_db"


def build(**overrides: str) -> Settings:
    """A Settings built from explicit values rather than the ambient environment."""
    values = {"ORDERS_DATABASE_URL": DATABASE_URL}
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


@pytest.mark.parametrize("alias", ["INVENTORY_TIMEOUT_S", "PAYMENTS_TIMEOUT_S"])
def test_a_timeout_below_the_floor_refuses_to_start(alias: str) -> None:
    with pytest.raises(ValidationError) as caught:
        build(**{alias: "0.25"})

    # The message has to name the setting, because the person reading it is looking at a
    # container that will not boot and needs to know which value to change.
    assert alias.lower() in str(caught.value).lower()


@pytest.mark.parametrize("alias", ["INVENTORY_TIMEOUT_S", "PAYMENTS_TIMEOUT_S"])
def test_the_floor_itself_is_allowed(alias: str) -> None:
    settings = build(**{alias: str(MIN_UPSTREAM_TIMEOUT_S)})

    assert getattr(settings, alias.lower()) == MIN_UPSTREAM_TIMEOUT_S


def test_the_documented_defaults_are_above_the_floor() -> None:
    """.env.example is what a new environment is copied from, so its values have to be
    ones the service will actually accept."""
    settings = build()

    assert settings.inventory_timeout_s == 2.0
    assert settings.payments_timeout_s == 3.0
    assert settings.inventory_timeout_s >= MIN_UPSTREAM_TIMEOUT_S
    assert settings.payments_timeout_s >= MIN_UPSTREAM_TIMEOUT_S


def test_a_generous_timeout_is_still_allowed() -> None:
    """The floor guards against too-small only. Deciding a dependency is worth waiting
    ten seconds for is a judgement call, not an error."""
    assert build(INVENTORY_TIMEOUT_S="10.0").inventory_timeout_s == 10.0


def test_an_expiry_window_at_or_near_zero_refuses_to_start() -> None:
    """INC-008: ORDER_EXPIRY_MINUTES=0 made every in-flight order eligible for expiry
    under its own request - the beat job and the request handler raced on the same row."""
    with pytest.raises(ValidationError) as caught:
        build(ORDER_EXPIRY_MINUTES="0")

    assert "order_expiry_minutes" in str(caught.value).lower()


def test_the_expiry_floor_itself_is_allowed() -> None:
    settings = build(ORDER_EXPIRY_MINUTES=str(MIN_ORDER_EXPIRY_MINUTES))

    assert settings.order_expiry_minutes == MIN_ORDER_EXPIRY_MINUTES


def test_the_documented_expiry_default_is_above_the_floor() -> None:
    assert build().order_expiry_minutes == 15
    assert build().order_expiry_minutes >= MIN_ORDER_EXPIRY_MINUTES
