"""The four logtool questions, plus a health snapshot and the incident board, as HTTP.

Endpoints stay thin on purpose: validate, call a service function, return it. The
`since` strings ('15m', '2h') are validated inside the service layer, which turns a bad
one into the platform's standard 422 envelope rather than a stack trace.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app import service
from app.config import Settings, get_settings
from app.schemas import (
    ErrorsOut,
    IncidentsOut,
    OrderStoryOut,
    OverviewOut,
    SlowOut,
    TraceOut,
)

router = APIRouter(prefix="/support", tags=["support"])

SettingsDep = Annotated[Settings, Depends(get_settings)]


@router.get("/overview", response_model=OverviewOut, summary="Live health of the platform")
def overview(settings: SettingsDep) -> OverviewOut:
    return service.probe(settings)


@router.get("/trace/{request_id}", response_model=TraceOut, summary="Follow one request")
def trace(request_id: str, settings: SettingsDep) -> TraceOut:
    return service.trace(settings, request_id)


@router.get("/errors", response_model=ErrorsOut, summary="Recent errors, grouped")
def errors(
    settings: SettingsDep,
    since: Annotated[str, Query(description="window, e.g. 15m, 2h, 1d")] = "15m",
) -> ErrorsOut:
    return service.errors(settings, since)


@router.get("/slow", response_model=SlowOut, summary="Slowest requests in a window")
def slow(
    settings: SettingsDep,
    since: Annotated[str, Query(description="window, e.g. 15m, 2h, 1d")] = "15m",
    top: Annotated[int, Query(ge=1, le=200)] = 10,
) -> SlowOut:
    return service.slow(settings, since, top)


@router.get("/order/{order_id}", response_model=OrderStoryOut, summary="One order's story")
def order_story(order_id: str, settings: SettingsDep) -> OrderStoryOut:
    return service.order_story(settings, order_id)


@router.get("/incidents", response_model=IncidentsOut, summary="The Phase 3 incident board")
def incidents(settings: SettingsDep) -> IncidentsOut:
    return service.incidents(settings)
