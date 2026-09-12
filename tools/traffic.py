"""Load generator for the local stack.

Requests are paced at the requested rate instead of being fired in a burst, otherwise
the latency numbers only measure how fast the machine can queue connections.

An order that comes back FAILED because stock ran out or the card was declined is the
system working as designed, so those are counted separately from transport errors and
5xx responses. Mixing the two makes the success rate meaningless.

    python tools/traffic.py --rps 10 --duration 120 --hot-sku SKU-0007
"""

from __future__ import annotations

import argparse
import asyncio
import math
import random
import sys
import time
import uuid
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import httpx

REQUEST_ID_HEADER = "X-Request-ID"

DEFAULT_ORDERS_URL = "http://localhost:8001"
DEFAULT_INVENTORY_URL = "http://localhost:8002"
DEFAULT_MIX = 0.7
DEFAULT_RPS = 10.0
DEFAULT_DURATION = 60.0
DEFAULT_TIMEOUT = 10.0

TICK_SECONDS = 0.1
LIST_SHARE = 0.4  # of browse requests; the rest fetch a single sku
HOT_SKU_SHARE = 0.5  # of orders, when --hot-sku is given
MAX_ITEMS_PER_ORDER = 3
MAX_QTY_PER_ITEM = 3

OK = "ok"
BUSINESS = "business_failed"
CLIENT = "client_error"
SERVER = "server_error"
TRANSPORT = "transport_error"
ERROR_OUTCOMES = (CLIENT, SERVER, TRANSPORT)

# Statuses an order can legitimately hold when POST /orders returns.
LIVE_ORDER_STATUSES = frozenset({"CONFIRMED", "RESERVED", "PENDING"})


@dataclass(frozen=True)
class Result:
    op: str
    outcome: str
    latency_ms: float
    status: int | None = None
    code: str | None = None
    request_id: str | None = None


def percentile(values: Sequence[float], pct: float) -> float:
    """Linear interpolation between the two nearest samples."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (pct / 100.0) * (len(ordered) - 1)
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return ordered[low]
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def classify(status: int | None, order_status: str | None) -> str:
    if status is None:
        return TRANSPORT
    if status >= 500:
        return SERVER
    if status >= 400:
        return CLIENT
    if order_status and order_status.upper() not in LIVE_ORDER_STATUSES:
        return BUSINESS
    return OK


def extract_skus(payload: Any) -> list[str]:
    """Pull skus out of whatever shape GET /products returns."""
    if isinstance(payload, dict):
        for key in ("items", "products", "results", "data"):
            nested = payload.get(key)
            if isinstance(nested, list):
                payload = nested
                break
    if not isinstance(payload, list):
        return []
    return [item["sku"] for item in payload if isinstance(item, dict) and _is_sku(item.get("sku"))]


def choose_skus(skus: Sequence[str], hot_sku: str | None, rng: random.Random) -> list[str]:
    """One to three distinct skus, with the hot sku over-represented when given."""
    pool = list(skus)
    wanted = min(rng.randint(1, MAX_ITEMS_PER_ORDER), len(pool))
    chosen: list[str] = []
    if hot_sku and rng.random() < HOT_SKU_SHARE:
        chosen.append(hot_sku)
        pool = [sku for sku in pool if sku != hot_sku]
    while len(chosen) < wanted and pool:
        chosen.append(pool.pop(rng.randrange(len(pool))))
    return chosen


def summarise(results: Sequence[Result], elapsed_s: float) -> dict[str, Any]:
    outcomes = Counter(result.outcome for result in results)
    errors = sum(outcomes[name] for name in ERROR_OUTCOMES)
    attempted = outcomes[OK] + errors
    latencies = [result.latency_ms for result in results]
    orders = [result for result in results if result.op == "order"]

    failures_by_status: Counter[str] = Counter()
    failures_by_code: Counter[str] = Counter()
    business_reasons: Counter[str] = Counter()
    examples: dict[str, str] = {}
    for result in results:
        if result.outcome == OK:
            continue
        code = result.code or "UNKNOWN"
        if result.outcome == BUSINESS:
            business_reasons[code] += 1
            continue
        failures_by_status[str(result.status) if result.status else "transport"] += 1
        failures_by_code[code] += 1
        if result.request_id and code not in examples:
            examples[code] = result.request_id

    return {
        "requests": len(results),
        "elapsed_s": round(elapsed_s, 2),
        "achieved_rps": round(len(results) / elapsed_s, 2) if elapsed_s > 0 else 0.0,
        "orders": len(orders),
        "by_outcome": dict(outcomes),
        "success_rate_pct": round(100.0 * outcomes[OK] / attempted, 2) if attempted else 0.0,
        "business_failure_pct": (
            round(100.0 * outcomes[BUSINESS] / len(orders), 2) if orders else 0.0
        ),
        "failures_by_status": dict(failures_by_status.most_common()),
        "failures_by_code": dict(failures_by_code.most_common()),
        "business_reasons": dict(business_reasons.most_common()),
        "examples": examples,
        "latency_ms": {
            "p50": round(percentile(latencies, 50), 1),
            "p95": round(percentile(latencies, 95), 1),
            "p99": round(percentile(latencies, 99), 1),
        },
    }


def format_summary(summary: dict[str, Any]) -> list[str]:
    outcomes = summary["by_outcome"]
    latency = summary["latency_ms"]
    lines = [
        "",
        f"requests           {summary['requests']} in {summary['elapsed_s']}s "
        f"({summary['achieved_rps']} rps achieved)",
        f"successful         {outcomes.get(OK, 0)} "
        f"({summary['success_rate_pct']}% of non-business requests)",
        f"business failures  {outcomes.get(BUSINESS, 0)} "
        f"({summary['business_failure_pct']}% of {summary['orders']} orders)",
        f"client errors      {outcomes.get(CLIENT, 0)}",
        f"server errors      {outcomes.get(SERVER, 0)}",
        f"transport errors   {outcomes.get(TRANSPORT, 0)}",
        f"latency ms         p50 {latency['p50']}  p95 {latency['p95']}  p99 {latency['p99']}",
    ]
    if summary["business_reasons"]:
        lines.append("")
        lines.append("business failures by reason")
        for reason, count in summary["business_reasons"].items():
            lines.append(f"  {reason:<40} {count}")
    if summary["failures_by_status"]:
        lines.append("")
        lines.append("failures by status")
        for status, count in summary["failures_by_status"].items():
            lines.append(f"  {status:<12} {count}")
    if summary["failures_by_code"]:
        lines.append("")
        lines.append("failures by code")
        for code, count in summary["failures_by_code"].items():
            example = summary["examples"].get(code)
            suffix = f"  (request_id {example})" if example else ""
            lines.append(f"  {code:<24} {count}{suffix}")
    return lines


def _is_sku(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)


def _json_body(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def _error_code(body: Any) -> str | None:
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and isinstance(error.get("code"), str):
            return error["code"]
    return None


async def _call(
    client: httpx.AsyncClient,
    op: str,
    method: str,
    url: str,
    *,
    json: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
) -> Result:
    started = time.perf_counter()
    try:
        response = await client.request(method, url, json=json, headers=headers)
    except httpx.HTTPError as exc:
        return Result(op=op, outcome=TRANSPORT, latency_ms=_ms(started), code=type(exc).__name__)

    latency_ms = _ms(started)
    body = _json_body(response)
    error_code = _error_code(body)
    order_status = None
    failure_reason = None
    if op == "order" and isinstance(body, dict):
        order_status = body.get("status") if isinstance(body.get("status"), str) else None
        failure_reason = body.get("failure_reason")

    outcome = classify(response.status_code, order_status)
    code = str(failure_reason or order_status) if outcome == BUSINESS else error_code
    return Result(
        op=op,
        outcome=outcome,
        latency_ms=latency_ms,
        status=response.status_code,
        code=code,
        request_id=response.headers.get(REQUEST_ID_HEADER),
    )


async def browse(
    client: httpx.AsyncClient,
    args: argparse.Namespace,
    skus: Sequence[str],
    rng: random.Random,
) -> Result:
    if rng.random() < LIST_SHARE:
        return await _call(client, "browse_list", "GET", f"{args.inventory_url}/products")
    sku = rng.choice(skus)
    return await _call(client, "browse_sku", "GET", f"{args.inventory_url}/products/{sku}")


async def place_order(
    client: httpx.AsyncClient,
    args: argparse.Namespace,
    skus: Sequence[str],
    rng: random.Random,
) -> Result:
    items = [
        {"sku": sku, "qty": rng.randint(1, MAX_QTY_PER_ITEM)}
        for sku in choose_skus(skus, args.hot_sku, rng)
    ]
    payload = {
        "customer_email": f"load{rng.randrange(1, 400)}@example.com",
        "items": items,
    }
    return await _call(
        client,
        "order",
        "POST",
        f"{args.orders_url}/orders",
        json=payload,
        headers={"Idempotency-Key": uuid.uuid4().hex},
    )


async def fetch_skus(inventory_url: str, timeout: float) -> list[str]:
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(f"{inventory_url}/products")
        response.raise_for_status()
        return extract_skus(_json_body(response))


async def run_load(args: argparse.Namespace, results: list[Result]) -> None:
    rng = random.Random(args.seed)
    skus = await fetch_skus(args.inventory_url, args.timeout)
    if not skus:
        raise RuntimeError(f"no products at {args.inventory_url}; run tools/seed.py first")
    if args.hot_sku and args.hot_sku not in skus:
        print(f"warning: --hot-sku {args.hot_sku} is not in the catalogue")

    print(
        f"{args.rps} rps for {args.duration}s, {args.mix:.0%} browse, "
        f"{len(skus)} skus" + (f", hot sku {args.hot_sku}" if args.hot_sku else "")
    )

    limits = httpx.Limits(max_connections=max(20, int(args.rps * 4)))
    async with httpx.AsyncClient(timeout=args.timeout, limits=limits) as client:
        tasks: set[asyncio.Task[None]] = set()
        loop = asyncio.get_running_loop()
        deadline = loop.time()
        end = deadline + args.duration
        budget = 0.0

        async def one() -> None:
            if rng.random() < args.mix:
                result = await browse(client, args, skus, rng)
            else:
                result = await place_order(client, args, skus, rng)
            results.append(result)

        while loop.time() < end:
            budget += args.rps * TICK_SECONDS
            launch = int(budget)
            budget -= launch
            for _ in range(launch):
                task = asyncio.create_task(one())
                tasks.add(task)
                task.add_done_callback(tasks.discard)
            deadline += TICK_SECONDS
            await asyncio.sleep(max(0.0, deadline - loop.time()))

        if tasks:
            # In-flight requests still count; wait for them before reporting latency.
            await asyncio.gather(*tasks, return_exceptions=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="traffic", description="generate browse and order traffic against the stack"
    )
    parser.add_argument("--rps", type=float, default=DEFAULT_RPS)
    parser.add_argument("--duration", type=float, default=DEFAULT_DURATION, help="seconds")
    parser.add_argument(
        "--mix", type=float, default=DEFAULT_MIX, help="fraction of requests that browse"
    )
    parser.add_argument("--hot-sku", default=None, help="sku that takes a large share of orders")
    parser.add_argument("--orders-url", default=DEFAULT_ORDERS_URL)
    parser.add_argument("--inventory-url", default=DEFAULT_INVENTORY_URL)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--seed", type=int, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.orders_url = args.orders_url.rstrip("/")
    args.inventory_url = args.inventory_url.rstrip("/")
    if args.rps <= 0 or args.duration <= 0:
        parser.error("--rps and --duration must be positive")
    if not 0.0 <= args.mix <= 1.0:
        parser.error("--mix must be between 0 and 1")

    results: list[Result] = []
    started = time.perf_counter()
    interrupted = False
    try:
        asyncio.run(run_load(args, results))
    except KeyboardInterrupt:
        interrupted = True
    except (httpx.HTTPError, RuntimeError) as exc:
        print(f"could not start: {exc}", file=sys.stderr)
        return 2
    elapsed = time.perf_counter() - started

    if interrupted:
        print("\ninterrupted, reporting what has run so far")
    if not results:
        print("no requests completed")
        return 1

    for line in format_summary(summarise(results, elapsed)):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
