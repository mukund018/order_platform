"""/health and /ready.

They answer different questions, and mixing them up is how you get a restart loop:

  /health  is the process alive?          -> never touches a dependency
  /ready   can it serve traffic right now? -> checks DB, Redis, ...

docker-compose healthchecks use /health for liveness and /ready for
`depends_on: condition: service_healthy`.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from .errors import DependencyUnavailableError, error_response

ReadinessCheck = Callable[[], Any]


def build_health_router(checks: Mapping[str, ReadinessCheck] | None = None) -> APIRouter:
    """`checks` maps a dependency name to a callable that raises when it is unhealthy.

    Checks may be sync or async; the sync ones are pushed to the threadpool so a
    blocking `SELECT 1` against a wedged database cannot stall the event loop.
    """
    router = APIRouter(tags=["health"])
    checks = dict(checks or {})

    @router.get("/health", summary="Liveness")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/ready", summary="Readiness")
    async def ready() -> JSONResponse:
        results: dict[str, str] = {}
        failed: list[str] = []

        for name, check in checks.items():
            try:
                if inspect.iscoroutinefunction(check):
                    await check()
                else:
                    await run_in_threadpool(check)
            except Exception as exc:  # any dependency failure means "not ready"
                results[name] = f"error: {type(exc).__name__}"
                failed.append(name)
            else:
                results[name] = "ok"

        if failed:
            error = DependencyUnavailableError(
                f"not ready: {', '.join(failed)}", details={"checks": results}
            )
            return error_response(
                error.status_code, error.code, error.message, details=error.details
            )

        return JSONResponse({"status": "ready", "checks": results})

    return router
