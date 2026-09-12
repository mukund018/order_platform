"""The three background jobs.

Every task here is a wrapper: open a session, call the domain function, close it. The
work itself lives in service.py so that a test can run it directly, with no broker and
no worker.
"""

import uuid
from datetime import date

from sqlalchemy.exc import OperationalError

from app import service
from app.celery_app import celery_app
from app.clients.inventory import get_inventory_client
from app.clients.payments import get_payments_client
from app.config import get_settings
from app.db import SessionLocal
from common.logging import get_logger

log = get_logger(__name__)

MAX_CONFIRMATION_RETRIES = 5
RETRY_BACKOFF_MAX_S = 300


@celery_app.task(
    name="app.tasks.send_confirmation",
    max_retries=MAX_CONFIRMATION_RETRIES,
    # Only a database that is momentarily unreachable is worth retrying. A refusal from
    # the domain function means the order should never get this mail, and backing off
    # five times would not change that.
    autoretry_for=(OperationalError,),
    retry_backoff=True,
    retry_backoff_max=RETRY_BACKOFF_MAX_S,
    retry_jitter=True,
)
def send_confirmation(order_id: str) -> None:
    with SessionLocal() as session:
        service.send_confirmation(session, uuid.UUID(order_id))


@celery_app.task(name="app.tasks.expire_stale_orders")
def expire_stale_orders() -> None:
    with SessionLocal() as session:
        service.expire_stale_orders(session, get_inventory_client())


@celery_app.task(name="app.tasks.reconcile_payment_mismatches")
def reconcile_payment_mismatches() -> None:
    with SessionLocal() as session:
        service.reconcile_payment_mismatches(session, get_payments_client())


@celery_app.task(name="app.tasks.daily_sales_report")
def daily_sales_report(day: str | None = None) -> None:
    """Yesterday's numbers, or a named day when someone reruns it by hand."""
    timezone = get_settings().business_timezone
    target = date.fromisoformat(day) if day else service.previous_business_day(timezone)

    with SessionLocal() as session:
        report = service.daily_report(session, target)

    log.info(
        "daily_sales_report",
        date=report.date.isoformat(),
        timezone=report.timezone,
        orders=report.orders,
        confirmed_orders=report.confirmed_orders,
        revenue_paise=report.revenue_paise,
    )
