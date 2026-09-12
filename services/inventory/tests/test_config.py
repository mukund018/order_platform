"""Configuration that would let the service lie to customers must not start.

Regression test for INC-002. `PRODUCT_CACHE_TTL` was 1800 in a deployed environment - half
an hour during which a restocked product kept reading as sold out - and nothing rejected
it, because the only bound on the field was `ge=1`. The invalidation half of that incident
was already covered by a test; this half was not.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import MAX_PRODUCT_CACHE_TTL, Settings

DATABASE_URL = "postgresql+psycopg://app:app@postgres:5432/inventory_db"


def build(**overrides: str) -> Settings:
    values = {"INVENTORY_DATABASE_URL": DATABASE_URL}
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def test_the_incident_value_is_refused() -> None:
    with pytest.raises(ValidationError) as caught:
        build(PRODUCT_CACHE_TTL="1800")

    assert "product_cache_ttl" in str(caught.value).lower()


def test_the_ceiling_itself_is_allowed() -> None:
    assert build(PRODUCT_CACHE_TTL=str(MAX_PRODUCT_CACHE_TTL)).product_cache_ttl == (
        MAX_PRODUCT_CACHE_TTL
    )


def test_zero_is_still_refused() -> None:
    """The lower bound predates INC-002 and has to survive it: a TTL of zero would mean
    every read is a database read, which is not a cache."""
    with pytest.raises(ValidationError):
        build(PRODUCT_CACHE_TTL="0")


def test_the_documented_default_is_inside_the_bounds() -> None:
    """.env.example is what a new environment is copied from."""
    settings = build()

    assert settings.product_cache_ttl == 60
    assert 1 <= settings.product_cache_ttl <= MAX_PRODUCT_CACHE_TTL
