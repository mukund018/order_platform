"""Stand-in for the third-party card gateway.

There is no real acquirer here, so this module fakes one: latency, declines and the
occasional call that hangs for longer than the caller is prepared to wait. Keeping all
of that behind one small class means the rest of the service is written against an
interface it could keep if a real gateway ever replaced it, and it keeps every source
of randomness in a single seeded generator so tests are reproducible.
"""

import random
import time
import uuid
from dataclasses import dataclass
from functools import lru_cache

from app.config import Settings, get_settings
from common.logging import get_logger

log = get_logger(__name__)

DECLINE_REASONS = ("insufficient_funds", "card_declined", "do_not_honour", "expired_card")


@dataclass(frozen=True)
class GatewayResult:
    approved: bool
    provider_ref: str | None
    failure_reason: str | None
    latency_ms: int


class Gateway:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._random = random.Random(settings.gateway_seed)

    def charge(self, order_id: uuid.UUID, amount_paise: int) -> GatewayResult:
        settings = self._settings
        roll = self._random.random()
        timeout_cut = settings.gateway_timeout_rate
        decline_cut = timeout_cut + settings.gateway_failure_rate

        if roll < timeout_cut:
            latency_ms = self._sleep_ms(settings.gateway_timeout_sleep_s * 1000)
            # The caller has long since timed out, but a real acquirer does not care:
            # it finishes the authorisation anyway. That mismatch - money taken for a
            # payment the caller recorded as a timeout - is the point of this knob.
            result = self._approve(latency_ms)
        elif roll < decline_cut:
            latency_ms = self._sleep_ms(self._latency_ms())
            result = GatewayResult(
                approved=False,
                provider_ref=None,
                failure_reason=self._random.choice(DECLINE_REASONS),
                latency_ms=latency_ms,
            )
        else:
            result = self._approve(self._sleep_ms(self._latency_ms()))

        log.debug(
            "gateway_charge",
            order_id=str(order_id),
            amount_paise=amount_paise,
            approved=result.approved,
            failure_reason=result.failure_reason,
            duration_ms=result.latency_ms,
        )
        return result

    def _approve(self, latency_ms: int) -> GatewayResult:
        return GatewayResult(
            approved=True,
            provider_ref=f"PAY-{self._random.getrandbits(48):012x}",
            failure_reason=None,
            latency_ms=latency_ms,
        )

    def _latency_ms(self) -> float:
        return self._random.uniform(
            self._settings.gateway_latency_ms_min, self._settings.gateway_latency_ms_max
        )

    def _sleep_ms(self, milliseconds: float) -> int:
        time.sleep(milliseconds / 1000)
        return int(milliseconds)


@lru_cache
def get_gateway() -> Gateway:
    """Reused across requests so one seeded generator drives the whole process."""
    return Gateway(get_settings())
