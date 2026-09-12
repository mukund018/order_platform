from collections.abc import Iterator

from prometheus_client import REGISTRY, Counter
from prometheus_client.core import GaugeMetricFamily
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings
from app.db import SessionLocal
from app.models import Product
from common.logging import get_logger

log = get_logger(__name__)

RESERVATION_FAILURES = Counter(
    "reservation_failures_total",
    "Reservation requests rejected, by reason",
    ["reason"],
)


class _StockCollector:
    """Reads stock for the tracked SKUs at scrape time rather than on every write."""

    def collect(self) -> Iterator[GaugeMetricFamily]:
        gauge = GaugeMetricFamily("product_stock", "Units available", labels=["sku"])
        skus = get_settings().tracked_skus
        if not skus:
            yield gauge
            return

        try:
            with SessionLocal() as session:
                rows = session.execute(
                    select(Product.sku, Product.stock).where(Product.sku.in_(skus))
                ).all()
        except SQLAlchemyError as exc:
            # A scrape must never fail loudly: prometheus would mark the whole
            # endpoint down and hide every other metric on it.
            log.warning("stock_gauge_unavailable", error=str(exc))
            rows = []

        for sku, stock in rows:
            gauge.add_metric([sku], float(stock))
        yield gauge


_stock_collector: _StockCollector | None = None


def register_stock_metrics() -> None:
    global _stock_collector
    if _stock_collector is None:
        _stock_collector = _StockCollector()
        REGISTRY.register(_stock_collector)
