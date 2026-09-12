"""Shared plumbing for the calls orders-service makes to the other two services.

Everything an upstream can do to us ends up as an AppError here: a timeout, a refused
connection, a 5xx, or a business answer like OUT_OF_STOCK. A 4xx keeps the upstream's own
error code, because the order flow branches on it; a 5xx does not, because their code
describes their fault and this service would then be reporting it as its own.
"""

import time
from typing import Any, NoReturn

import httpx
from pydantic import BaseModel, ValidationError

from common.errors import AppError, UpstreamError, UpstreamTimeoutError
from common.logging import get_logger
from common.request_id import httpx_event_hooks

log = get_logger(__name__)

# The width of orders.failure_reason, which is where an upstream message ends up.
MAX_MESSAGE_LENGTH = 200


def build_client(base_url: str, timeout: float) -> httpx.Client:
    """One client per upstream, reused for the life of the process.

    The timeout is passed explicitly on purpose: httpx's default of five seconds is
    longer than we are willing to keep a customer waiting, and passing None waits forever.
    """
    return httpx.Client(
        base_url=base_url,
        timeout=timeout,
        event_hooks=httpx_event_hooks(),
        headers={"Accept": "application/json"},
    )


def call(
    client: httpx.Client,
    upstream: str,
    method: str,
    path: str,
    *,
    json: Any = None,
) -> Any:
    started = time.perf_counter()
    try:
        response = client.request(method, path, json=json)
    except httpx.TimeoutException as exc:
        log.error(
            "upstream_timeout",
            upstream=upstream,
            method=method,
            path=path,
            duration_ms=_elapsed_ms(started),
            timeout_s=client.timeout.read,
        )
        raise UpstreamTimeoutError(
            f"{upstream} did not answer within the timeout",
            details={"upstream": upstream, "path": path},
        ) from exc
    except httpx.RequestError as exc:
        log.error(
            "upstream_unreachable",
            upstream=upstream,
            method=method,
            path=path,
            duration_ms=_elapsed_ms(started),
            error=str(exc),
        )
        raise UpstreamError(
            f"{upstream} is unreachable", details={"upstream": upstream, "path": path}
        ) from exc

    level = log.info if response.is_success else log.warning
    level(
        "upstream_call",
        upstream=upstream,
        method=method,
        path=path,
        status_code=response.status_code,
        duration_ms=_elapsed_ms(started),
    )

    if not response.is_success:
        _raise_for_response(response, upstream, path)
    return _decode(response, upstream, path)


def parse[ModelT: BaseModel](model: type[ModelT], payload: Any, upstream: str) -> ModelT:
    try:
        return model.model_validate(payload)
    except ValidationError as exc:
        log.error("upstream_contract_broken", upstream=upstream, error=str(exc))
        raise UpstreamError(
            f"{upstream} returned a body we do not understand", details={"upstream": upstream}
        ) from exc


def _raise_for_response(response: httpx.Response, upstream: str, path: str) -> NoReturn:
    details = {"upstream": upstream, "path": path}
    error = _envelope(response)
    if error is None:
        raise UpstreamError(f"{upstream} returned {response.status_code}", details=details)

    message = str(error.get("message") or f"{upstream} returned {response.status_code}")
    if response.status_code >= 500:
        # Their 5xx is our 502, and their code goes into details rather than into ours:
        # answering with INTERNAL_ERROR would have everyone grepping for a bug in
        # orders-service when the service that broke is named right here.
        details["upstream_code"] = str(error["code"])
        raise UpstreamError(message[:MAX_MESSAGE_LENGTH], details=details)

    raise AppError(
        message[:MAX_MESSAGE_LENGTH],
        code=str(error["code"]),
        status_code=response.status_code,
        details=details,
    )


def _envelope(response: httpx.Response) -> dict[str, Any] | None:
    """The {"error": {"code", "message"}} body every service in the platform returns."""
    body = _json_or_none(response)
    if not isinstance(body, dict):
        return None
    error = body.get("error")
    if isinstance(error, dict) and isinstance(error.get("code"), str):
        return error
    return None


def _decode(response: httpx.Response, upstream: str, path: str) -> Any:
    body = _json_or_none(response)
    if body is None:
        raise UpstreamError(
            f"{upstream} returned a non-JSON body", details={"upstream": upstream, "path": path}
        )
    return body


def _json_or_none(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)
