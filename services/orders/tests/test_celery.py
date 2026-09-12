from collections.abc import Iterator
from typing import Any

import pytest
from celery.signals import (
    before_task_publish,
    task_failure,
    task_postrun,
    task_prerun,
    task_retry,
    task_success,
    worker_ready,
)
from celery.utils.dispatch import Signal
from prometheus_client import REGISTRY

from app import celery_app
from app.config import get_settings
from app.tasks import send_confirmation
from common.request_id import REQUEST_ID_HEADER, bind_request_id, clear_context, get_request_id

TASK_NAME = "app.tasks.send_confirmation"


@pytest.fixture(autouse=True)
def _clear_context() -> Iterator[None]:
    clear_context()
    yield
    clear_context()


def task_counter(result: str) -> float:
    value = REGISTRY.get_sample_value("celery_tasks_total", {"task": TASK_NAME, "result": result})
    return value or 0.0


def test_the_request_id_rides_along_with_the_message() -> None:
    bind_request_id("publish-trace-01")
    headers: dict[str, Any] = {}

    before_task_publish.send(sender=TASK_NAME, headers=headers)

    assert headers[REQUEST_ID_HEADER] == "publish-trace-01"


def test_a_message_queued_outside_a_request_carries_no_id() -> None:
    headers: dict[str, Any] = {}

    before_task_publish.send(sender=TASK_NAME, headers=headers)

    assert headers == {}


def test_a_task_picks_up_the_id_of_the_request_that_queued_it() -> None:
    send_confirmation.push_request(**{REQUEST_ID_HEADER: "worker-trace-01"})
    try:
        task_prerun.send(sender=send_confirmation, task=send_confirmation)
        assert get_request_id() == "worker-trace-01"
    finally:
        send_confirmation.pop_request()


def test_a_task_started_by_beat_mints_its_own_id() -> None:
    send_confirmation.push_request()
    try:
        task_prerun.send(sender=send_confirmation, task=send_confirmation)
        assert get_request_id()
    finally:
        send_confirmation.pop_request()


def test_the_context_is_dropped_when_the_task_ends() -> None:
    bind_request_id("worker-trace-02")

    task_postrun.send(sender=send_confirmation)

    assert get_request_id() is None


@pytest.mark.parametrize(
    ("signal", "result"),
    [(task_success, "success"), (task_retry, "retry"), (task_failure, "failure")],
)
def test_every_task_outcome_is_counted(signal: Signal, result: str) -> None:
    before = task_counter(result)

    signal.send(sender=send_confirmation)

    assert task_counter(result) == before + 1


def test_the_worker_serves_its_metrics_once(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[int] = []
    monkeypatch.setattr(celery_app, "start_http_server", started.append)
    monkeypatch.setattr(celery_app, "_metrics_started", False)

    worker_ready.send(sender=None)
    worker_ready.send(sender=None)

    assert started == [9100]


def test_a_metrics_port_already_in_use_does_not_stop_the_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: list[int] = []

    def refuse_once(port: int) -> None:
        raise OSError("address already in use")

    monkeypatch.setattr(celery_app, "start_http_server", refuse_once)
    monkeypatch.setattr(celery_app, "_metrics_started", False)

    worker_ready.send(sender=None)

    # A signal receiver that raises is swallowed by celery, so "nothing escaped" proves
    # nothing. What matters is that the worker did not record a server it never started.
    assert celery_app._metrics_started is False

    monkeypatch.setattr(celery_app, "start_http_server", started.append)
    worker_ready.send(sender=None)

    assert started == [9100]
    assert celery_app._metrics_started is True


def test_the_beat_schedule_is_the_one_the_dashboards_assume() -> None:
    schedule = celery_app.celery_app.conf.beat_schedule

    assert schedule["expire-stale-orders"]["schedule"] == 60.0
    report = schedule["daily-sales-report"]["schedule"]
    assert (report.hour, report.minute) == ({0}, {5})


def test_the_daily_report_is_scheduled_on_the_business_clock() -> None:
    # 00:05 in the store's timezone, not 00:05 UTC - which in IST is 05:35 the same
    # morning, after the day's orders have started coming in.
    assert celery_app.celery_app.conf.timezone == get_settings().business_timezone
