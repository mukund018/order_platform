import uuid
from collections.abc import Sequence
from functools import lru_cache

import httpx
from pydantic import BaseModel

from app.clients.base import build_client, call, parse
from app.config import get_settings

UPSTREAM = "inventory"


class ProductView(BaseModel):
    id: uuid.UUID
    sku: str
    name: str
    price_paise: int
    stock: int


class ReservationLine(BaseModel):
    sku: str
    qty: int


class ReservationView(BaseModel):
    order_id: uuid.UUID
    status: str
    items: list[ReservationLine]


class InventoryClient:
    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def get_product(self, sku: str) -> ProductView:
        payload = call(self._client, UPSTREAM, "GET", f"/products/{sku}")
        return parse(ProductView, payload, UPSTREAM)

    def get_products(self, skus: Sequence[str]) -> dict[str, ProductView]:
        """One call per sku. An order has a handful of lines and inventory serves these
        from its redis cache, so the round trips are cheaper than pulling the catalogue.
        """
        return {sku: self.get_product(sku) for sku in skus}

    def reserve(self, order_id: uuid.UUID, items: Sequence[ReservationLine]) -> ReservationView:
        payload = call(
            self._client,
            UPSTREAM,
            "POST",
            "/reservations",
            json={
                "order_id": str(order_id),
                "items": [item.model_dump() for item in items],
            },
        )
        return parse(ReservationView, payload, UPSTREAM)

    def commit(self, order_id: uuid.UUID) -> ReservationView:
        payload = call(self._client, UPSTREAM, "POST", f"/reservations/{order_id}/commit")
        return parse(ReservationView, payload, UPSTREAM)

    def release(self, order_id: uuid.UUID) -> ReservationView:
        payload = call(self._client, UPSTREAM, "POST", f"/reservations/{order_id}/release")
        return parse(ReservationView, payload, UPSTREAM)


@lru_cache
def get_inventory_client() -> InventoryClient:
    settings = get_settings()
    return InventoryClient(build_client(settings.inventory_url, settings.inventory_timeout_s))
