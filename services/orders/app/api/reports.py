from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app import service
from app.db import get_session
from app.schemas import DailyReport

router = APIRouter(prefix="/reports", tags=["reports"])


@router.get("/daily", response_model=DailyReport, summary="Sales for one business day")
def daily_report(
    day: Annotated[date, Query(alias="date", description="Business day, YYYY-MM-DD")],
    session: Session = Depends(get_session),
) -> DailyReport:
    # The day is a calendar day in the store's timezone, not a UTC slice. The window the
    # numbers came from is in the response so nobody has to guess which it was.
    return service.daily_report(session, day)
