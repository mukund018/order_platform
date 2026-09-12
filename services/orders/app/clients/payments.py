import uuid
from functools import lru_cache

import httpx
from pydantic import BaseModel

from app.clients.base import build_client, call, parse
from app.config import get_settings
from common.errors import AppError

UPSTREAM = "payments"

SUCCEEDED = "SUCCEEDED"


class PaymentView(BaseModel):
    id: uuid.UUID
    order_id: uuid.UUID
    amount_paise: int
    status: str
    provider_ref: str | None = None
    failure_reason: str | None = None
    attempts: int = 0

    @property
    def succeeded(self) -> bool:
        return self.status == SUCCEEDED


class PaymentsClient:
    def __init__(self, client: httpx.Client) -> None:
        self._client = client

    def charge(self, order_id: uuid.UUID, amount_paise: int) -> PaymentView:
        """A decline comes back as 200 with status FAILED, not as an HTTP error."""
        payload = call(
            self._client,
            UPSTREAM,
            "POST",
            "/payments",
            json={"order_id": str(order_id), "amount_paise": amount_paise},
        )
        return parse(PaymentView, payload, UPSTREAM)

    def get_status(self, order_id: uuid.UUID) -> PaymentView | None:
        """None means payments has no record of this order at all - not the same thing
        as a FAILED payment, which does have a row. Used for reconciliation only; the
        checkout flow itself never needs to ask "what happened" after the fact."""
        try:
            payload = call(self._client, UPSTREAM, "GET", f"/payments/{order_id}")
        except AppError as exc:
            if exc.code == "NOT_FOUND":
                return None
            raise
        return parse(PaymentView, payload, UPSTREAM)


@lru_cache
def get_payments_client() -> PaymentsClient:
    settings = get_settings()
    return PaymentsClient(build_client(settings.payments_url, settings.payments_timeout_s))
