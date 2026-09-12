from collections.abc import Callable
from typing import Any

import fakeredis
from fastapi.testclient import TestClient
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import update
from sqlalchemy.orm import Session

from app import cache as cache_module
from app.cache import LIST_KEY, ProductCache, product_key
from app.models import Product


class BrokenRedis:
    """Stands in for a redis that is down: every call raises."""

    def get(self, *args: Any, **kwargs: Any) -> Any:
        raise RedisConnectionError("connection refused")

    def setex(self, *args: Any, **kwargs: Any) -> Any:
        raise RedisConnectionError("connection refused")

    def delete(self, *args: Any, **kwargs: Any) -> Any:
        raise RedisConnectionError("connection refused")

    def ping(self, *args: Any, **kwargs: Any) -> Any:
        raise RedisConnectionError("connection refused")


def _silently_set_stock(session: Session, sku: str, stock: int) -> None:
    """Change the database behind the cache's back, so a stale read is visible."""
    session.execute(update(Product).where(Product.sku == sku).values(stock=stock))
    session.commit()


def test_product_round_trip_and_ttl() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    cache = ProductCache(redis, ttl=45)

    cache.set_product("SKU-0001", {"sku": "SKU-0001", "stock": 2})

    assert cache.get_product("SKU-0001") == {"sku": "SKU-0001", "stock": 2}
    # TTL is reported in whole seconds and has already started counting down, so the
    # only safe assertion is that an expiry was set at all and it is the right size.
    assert 0 < redis.ttl(product_key("SKU-0001")) <= 45


def test_invalidate_drops_the_product_and_the_list() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    cache = ProductCache(redis, ttl=45)
    cache.set_product("SKU-0001", {"sku": "SKU-0001"})
    cache.set_list([{"sku": "SKU-0001"}])

    cache.invalidate("SKU-0001")

    assert cache.get_product("SKU-0001") is None
    assert cache.get_list() is None
    assert redis.exists(LIST_KEY) == 0


def test_undecodable_value_is_treated_as_a_miss() -> None:
    redis = fakeredis.FakeRedis(decode_responses=True)
    redis.set(LIST_KEY, "not json")
    cache = ProductCache(redis, ttl=45)

    assert cache.get_list() is None
    assert redis.exists(LIST_KEY) == 0


def test_list_is_served_from_the_cache_on_the_second_call(
    client: TestClient, session: Session, make_product: Callable[..., Product]
) -> None:
    make_product("SKU-0001", stock=10)
    assert client.get("/products").json()[0]["stock"] == 10

    _silently_set_stock(session, "SKU-0001", 99)

    assert client.get("/products").json()[0]["stock"] == 10


def test_single_product_is_served_from_the_cache(
    client: TestClient, session: Session, make_product: Callable[..., Product]
) -> None:
    make_product("SKU-0001", stock=10)
    assert client.get("/products/SKU-0001").json()["stock"] == 10

    _silently_set_stock(session, "SKU-0001", 99)

    assert client.get("/products/SKU-0001").json()["stock"] == 10


def test_stock_adjustment_invalidates_both_keys(
    client: TestClient, make_product: Callable[..., Product]
) -> None:
    make_product("SKU-0001", stock=10)
    client.get("/products")
    client.get("/products/SKU-0001")

    client.patch("/products/SKU-0001/stock", json={"delta": 5})

    assert client.get("/products/SKU-0001").json()["stock"] == 15
    assert client.get("/products").json()[0]["stock"] == 15


def test_new_product_appears_in_a_warmed_list(
    client: TestClient, make_product: Callable[..., Product]
) -> None:
    make_product("SKU-0001")
    assert len(client.get("/products").json()) == 1

    client.post(
        "/products", json={"sku": "SKU-0002", "name": "Lamp", "price_paise": 9900, "stock": 2}
    )

    assert len(client.get("/products").json()) == 2


def test_reservation_invalidates_the_cached_stock(
    client: TestClient, make_product: Callable[..., Product]
) -> None:
    make_product("SKU-0001", stock=10)
    client.get("/products")

    client.post(
        "/reservations",
        json={
            "order_id": "11111111-1111-1111-1111-111111111111",
            "items": [{"sku": "SKU-0001", "qty": 4}],
        },
    )

    assert client.get("/products").json()[0]["stock"] == 6


def test_a_dead_redis_falls_back_to_the_database(
    client: TestClient, make_product: Callable[..., Product]
) -> None:
    make_product("SKU-0001", stock=7)
    cache_module.set_cache(ProductCache(BrokenRedis(), ttl=45))

    assert client.get("/products").json()[0]["stock"] == 7
    assert client.get("/products/SKU-0001").json()["stock"] == 7
    assert client.patch("/products/SKU-0001/stock", json={"delta": 1}).status_code == 200


def test_a_stock_write_is_visible_on_the_very_next_read(
    client: TestClient, make_product: Callable[..., Product]
) -> None:
    """INC-002, stated as behaviour rather than as caching.

    `test_stock_adjustment_invalidates_both_keys` above already covers this and already
    failed against the incident - verified by re-applying the fault. This one exists
    because that test is named after the *mechanism*: if the caching strategy ever changes
    to write-through, or to a different key layout, that name stops describing anything a
    customer cares about. The promise that has to survive any such change is this one.
    """
    make_product("SKU-0001", stock=0)
    # A customer browses the sold-out product, which is what populates the cache.
    assert client.get("/products/SKU-0001").json()["stock"] == 0
    assert client.get("/products").json()[0]["stock"] == 0

    client.patch("/products/SKU-0001/stock", json={"delta": 60})

    # No sleep, no second attempt: the very next read has to tell the truth.
    assert client.get("/products/SKU-0001").json()["stock"] == 60
    assert client.get("/products").json()[0]["stock"] == 60
