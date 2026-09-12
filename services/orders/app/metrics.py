from collections.abc import Iterator
from datetime import timedelta

from prometheus_client import REGISTRY, Counter
from prometheus_client.core import GaugeMetricFamily
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from app.db import SessionLocal
from app.models import UNFINISHED_STATUSES, Order, OrderStatus, utcnow
from common.logging import get_logger

log = get_logger(__name__)

# An order that has not reached CONFIRMED, FAILED or EXPIRED after this long is stuck,
# not slow. Five minutes is well past the payment timeout plus every retry.
STALE_AFTER = timedelta(minutes=5)

ORDERS_TOTAL = Counter(
    "orders_total",
    "Orders entering each status",
    ["status"],
)

CELERY_TASKS = Counter(
    "celery_tasks_total",
    "Celery task outcomes",
    ["task", "result"],
)

# INC-001 was a client-side timeout: inventory was healthy and orders was hanging up on
# it, so every http_requests_total series on both services looked normal while 2% of
# checkouts failed. The outcome of an outbound call was only ever written to the logs.
# This is the same fact as a metric, labelled by which dependency and how the call ended.
UPSTREAM_CALLS = Counter(
    "upstream_calls_total",
    "Outbound calls to another service, by how they ended",
    ["upstream", "outcome"],
)

# INC-004: an order marked FAILED on a payment timeout where payments-service actually
# completed the charge. Any nonzero value here is real money held against a customer
# who was told the order failed - this should page someone, not wait for a dashboard.
PAYMENT_RECONCILIATION_MISMATCH = Counter(
    "payment_reconciliation_mismatch_total",
    "Orders marked FAILED on a payment timeout that payments-service actually charged",
)


class _OrderCollector:
    """Counts orders at scrape time.

    These are gauges over the whole table, not something a request can increment, so
    they have to be read when prometheus asks. Registered by the API process only: the
    worker scrapes the same registry, and two processes exporting the same gauge would
    make every `sum by (status)` panel show double.
    """

    def collect(self) -> Iterator[GaugeMetricFamily]:
        by_status = GaugeMetricFamily(
            "orders_by_status", "Orders currently in each status", labels=["status"]
        )
        stale = GaugeMetricFamily(
            "stale_pending_orders", "Orders held before CONFIRMED for longer than five minutes"
        )

        try:
            with SessionLocal() as session:
                counts = dict(
                    session.execute(
                        select(Order.status, func.count(Order.id)).group_by(Order.status)
                    ).all()
                )
                stuck = session.scalar(
                    select(func.count(Order.id)).where(
                        Order.status.in_(UNFINISHED_STATUSES),
                        Order.created_at < utcnow() - STALE_AFTER,
                    )
                )
        except SQLAlchemyError as exc:
            # A scrape must never fail loudly: prometheus would mark the whole endpoint
            # down and hide every other metric on it, exactly when we need them.
            log.warning("order_gauges_unavailable", error=str(exc))
            yield by_status
            yield stale
            return

        for status in OrderStatus:
            # Export every status, including the empty ones, so a panel reads zero
            # rather than going blank when nothing has failed yet.
            by_status.add_metric([status.value], float(counts.get(status, 0)))
        stale.add_metric([], float(stuck or 0))

        yield by_status
        yield stale


_collector: _OrderCollector | None = None


def register_order_metrics() -> None:
    global _collector
    if _collector is None:
        _collector = _OrderCollector()
        REGISTRY.register(_collector)
