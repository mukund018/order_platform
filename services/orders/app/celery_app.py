"""The celery app, the beat schedule, and the signals that make the worker behave like
the HTTP services: same JSON logs, same request id, same prometheus counters.

Without the request-id signals a task is an island - you can see that a confirmation
failed, but not which order request put it on the queue.
"""

from typing import Any

from celery import Celery
from celery.schedules import crontab
from celery.signals import (
    before_task_publish,
    setup_logging,
    task_failure,
    task_postrun,
    task_prerun,
    task_retry,
    task_success,
    worker_ready,
)
from prometheus_client import start_http_server

from app.config import get_settings
from app.metrics import CELERY_TASKS
from common.logging import configure_logging, get_logger
from common.request_id import REQUEST_ID_HEADER, bind_request_id, clear_context, get_request_id

SERVICE_NAME = "worker"

EXPIRY_INTERVAL_S = 60.0
REPORT_HOUR = 0
REPORT_MINUTE = 5

log = get_logger(__name__)

settings = get_settings()

celery_app = Celery("orders", broker=settings.celery_broker_url)
celery_app.conf.update(
    # Nothing ever reads a task result, and a backend would write every one of them to
    # redis and then expire it again for no reader.
    result_backend=None,
    task_ignore_result=True,
    # Acknowledge after the task, not before: a worker killed mid-task gives the message
    # back to the queue instead of dropping it. Every task here is safe to run twice.
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    # crontab entries below are evaluated in this zone; messages still carry UTC.
    timezone=settings.business_timezone,
    enable_utc=True,
    include=["app.tasks"],
    beat_schedule={
        "expire-stale-orders": {
            "task": "app.tasks.expire_stale_orders",
            "schedule": EXPIRY_INTERVAL_S,
        },
        "daily-sales-report": {
            "task": "app.tasks.daily_sales_report",
            "schedule": crontab(hour=REPORT_HOUR, minute=REPORT_MINUTE),
        },
    },
)

_metrics_started = False


@setup_logging.connect
def _configure_worker_logging(**_: Any) -> None:
    """Connecting to this signal stops celery installing its own handlers, which would
    otherwise turn every worker line back into unparseable plain text."""
    configure_logging(SERVICE_NAME, level=settings.log_level, log_dir=settings.log_dir)


@worker_ready.connect
def _start_metrics_server(**_: Any) -> None:
    global _metrics_started
    if _metrics_started:
        return

    port = settings.metrics_port
    try:
        start_http_server(port)
    except OSError as exc:
        # Losing worker metrics is bad; refusing to process any task because a port was
        # taken is worse.
        log.warning("metrics_server_unavailable", port=port, error=str(exc))
        return

    _metrics_started = True
    log.info("metrics_server_started", port=port)


@before_task_publish.connect
def _forward_request_id(headers: dict[str, Any] | None = None, **_: Any) -> None:
    request_id = get_request_id()
    if headers is not None and request_id:
        headers[REQUEST_ID_HEADER] = request_id


@task_prerun.connect
def _bind_task_context(task: Any = None, **_: Any) -> None:
    request = getattr(task, "request", None)
    incoming = request.get(REQUEST_ID_HEADER) if request is not None else None
    # A task started by beat has no caller, so it mints its own id.
    bind_request_id(incoming)


@task_postrun.connect
def _clear_task_context(**_: Any) -> None:
    clear_context()


@task_success.connect
def _count_success(sender: Any = None, **_: Any) -> None:
    CELERY_TASKS.labels(task=_task_name(sender), result="success").inc()


@task_retry.connect
def _count_retry(sender: Any = None, **_: Any) -> None:
    CELERY_TASKS.labels(task=_task_name(sender), result="retry").inc()


@task_failure.connect
def _count_failure(sender: Any = None, **_: Any) -> None:
    CELERY_TASKS.labels(task=_task_name(sender), result="failure").inc()


def _task_name(sender: Any) -> str:
    return getattr(sender, "name", "unknown")
