"""Cache-aside product cache.

The cache is an optimisation. It must never be able to take the service down, so every
redis call here is wrapped: on failure we log a warning and fall back to the database.
"""

import json
import random
from typing import Any

from redis import Redis
from redis.exceptions import RedisError

from app.config import get_settings
from common.logging import get_logger

log = get_logger(__name__)

LIST_KEY = "inventory:products:all"

# INC-012: every product key written around the same moment expires at the same
# moment, so a TTL short enough to matter (paired with a pool small enough to matter)
# turns into every in-flight request missing the cache at once - a stampede, not a
# steady trickle of misses. +/-20% jitter spreads expiry out so keys do not fall due
# in lockstep, without meaningfully changing how fresh the cache is on average.
JITTER_FRACTION = 0.2


def product_key(sku: str) -> str:
    return f"inventory:product:{sku}"


class ProductCache:
    def __init__(self, client: Redis, *, ttl: int) -> None:
        self._client = client
        self._ttl = ttl

    def get_list(self) -> list[dict[str, Any]] | None:
        payload = self._read(LIST_KEY)
        return payload if isinstance(payload, list) else None

    def set_list(self, products: list[dict[str, Any]]) -> None:
        self._write(LIST_KEY, products)

    def get_product(self, sku: str) -> dict[str, Any] | None:
        payload = self._read(product_key(sku))
        return payload if isinstance(payload, dict) else None

    def set_product(self, sku: str, product: dict[str, Any]) -> None:
        self._write(product_key(sku), product)

    def invalidate(self, sku: str) -> None:
        """Drop the product and the list it appears in. Safe to call for any sku."""
        self._drop(product_key(sku), LIST_KEY)

    def ping(self) -> None:
        """Raises when redis is unreachable. Used by /ready, so it does not swallow."""
        self._client.ping()

    def _read(self, key: str) -> Any:
        try:
            raw = self._client.get(key)
        except RedisError as exc:
            log.warning("cache_read_failed", cache_key=key, error=str(exc))
            return None
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except ValueError:
            # A value we cannot decode would keep failing until the ttl expires.
            log.warning("cache_decode_failed", cache_key=key)
            self._drop(key)
            return None

    def _write(self, key: str, payload: Any) -> None:
        try:
            self._client.setex(key, self._jittered_ttl(), json.dumps(payload))
        except RedisError as exc:
            log.warning("cache_write_failed", cache_key=key, error=str(exc))

    def _jittered_ttl(self) -> int:
        spread = int(self._ttl * JITTER_FRACTION)
        return self._ttl if spread == 0 else self._ttl + random.randint(-spread, spread)

    def _drop(self, *keys: str) -> None:
        try:
            self._client.delete(*keys)
        except RedisError as exc:
            log.warning("cache_delete_failed", cache_keys=list(keys), error=str(exc))


_cache: ProductCache | None = None


def get_cache() -> ProductCache:
    global _cache
    if _cache is None:
        settings = get_settings()
        _cache = ProductCache(
            # A redis that hangs must not hang the request behind it. One second is
            # already far longer than a cache read has any business taking.
            Redis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_timeout=1.0,
                socket_connect_timeout=1.0,
            ),
            ttl=settings.product_cache_ttl,
        )
    return _cache


def set_cache(cache: ProductCache | None) -> None:
    """Replace the process-wide cache (tests inject fakeredis through this)."""
    global _cache
    _cache = cache
