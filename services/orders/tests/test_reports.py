from collections.abc import Callable
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from freezegun import freeze_time
from sqlalchemy.orm import Session

from app import service
from app.models import Order, OrderStatus

IST = "Asia/Kolkata"
DAY = date(2025, 11, 12)


def ist(hour: int, minute: int, day: date = DAY) -> datetime:
    """A wall-clock time in the store's timezone, stored the way the database sees it."""
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=ZoneInfo(IST)).astimezone(
        UTC
    )


def test_a_business_day_runs_from_1830_utc_to_1830_utc() -> None:
    start, end = service.business_day_bounds(DAY, IST)

    assert start == datetime(2025, 11, 11, 18, 30, tzinfo=UTC)
    assert end == datetime(2025, 11, 12, 18, 30, tzinfo=UTC)


def test_consecutive_days_meet_exactly_once() -> None:
    _, first_end = service.business_day_bounds(DAY, IST)
    second_start, _ = service.business_day_bounds(date(2025, 11, 13), IST)

    # Half open: 18:30 UTC belongs to the 13th and to nothing else.
    assert first_end == second_start


def test_an_order_just_after_midnight_ist_belongs_to_the_new_day(
    session: Session, make_order: Callable[..., Order]
) -> None:
    make_order(status=OrderStatus.CONFIRMED, created_at=ist(0, 30), unit_price_paise=10000)

    assert service.daily_report(session, DAY).confirmed_orders == 1
    # 00:30 IST on the 12th is 19:00 UTC on the 11th. Slicing on the UTC date would put
    # it here.
    assert service.daily_report(session, date(2025, 11, 11)).confirmed_orders == 0


def test_an_order_late_in_the_evening_ist_belongs_to_the_same_day(
    session: Session, make_order: Callable[..., Order]
) -> None:
    make_order(status=OrderStatus.CONFIRMED, created_at=ist(23, 30), unit_price_paise=10000)

    assert service.daily_report(session, DAY).confirmed_orders == 1
    assert service.daily_report(session, date(2025, 11, 13)).confirmed_orders == 0


def test_only_confirmed_orders_earn_revenue(
    session: Session, make_order: Callable[..., Order]
) -> None:
    make_order(status=OrderStatus.CONFIRMED, created_at=ist(12, 0), unit_price_paise=10000)
    make_order(status=OrderStatus.FAILED, created_at=ist(13, 0), unit_price_paise=50000)
    make_order(status=OrderStatus.CANCELLED, created_at=ist(14, 0), unit_price_paise=70000)

    report = service.daily_report(session, DAY)

    assert report.orders == 3
    assert report.confirmed_orders == 1
    assert report.revenue_paise == 10000


def test_a_day_with_no_orders_reports_zeroes(session: Session) -> None:
    report = service.daily_report(session, DAY)

    assert report.orders == 0
    assert report.revenue_paise == 0


@freeze_time("2025-11-12T19:30:00Z")
def test_the_previous_business_day_is_the_local_one() -> None:
    # 19:30 UTC is 01:00 IST on the 13th, so the day that just ended is the 12th - which
    # is still today in UTC.
    assert service.previous_business_day(IST) == DAY


def test_the_endpoint_reports_the_window_it_used(
    client: TestClient, make_order: Callable[..., Order]
) -> None:
    make_order(status=OrderStatus.CONFIRMED, created_at=ist(0, 30), unit_price_paise=10000)

    response = client.get("/reports/daily", params={"date": "2025-11-12"})

    assert response.status_code == 200
    body = response.json()
    assert body["date"] == "2025-11-12"
    assert body["timezone"] == IST
    assert datetime.fromisoformat(body["window_start"]) == datetime(
        2025, 11, 11, 18, 30, tzinfo=UTC
    )
    assert body["confirmed_orders"] == 1
    assert body["revenue_paise"] == 10000


def test_the_endpoint_rejects_a_date_it_cannot_read(client: TestClient) -> None:
    response = client.get("/reports/daily", params={"date": "12-11-2025"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


def test_the_endpoint_needs_a_date(client: TestClient) -> None:
    assert client.get("/reports/daily").status_code == 422
