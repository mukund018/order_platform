# API

Interactive docs are served per service: [orders](http://localhost:8001/docs),
[inventory](http://localhost:8002/docs), [payments](http://localhost:8003/docs). This page
covers the things the generated docs do not say — error codes, idempotency, and which
non-2xx responses are actually normal.

## Conventions

Every service has `GET /health`, `GET /ready` and `GET /metrics`. Money is always integer
paise. Ids are UUIDs. Timestamps are ISO 8601 UTC.

**Every non-2xx response has the same body:**

```json
{
  "error": {
    "code": "OUT_OF_STOCK",
    "message": "SKU-0003 has 2 units, 5 requested",
    "request_id": "b17c4e2a9f0d4c1e8a3b5d7f2e6c9a04",
    "details": {"sku": "SKU-0003", "available": 2, "requested": 5}
  }
}
```

`code` is the contract — orders-service branches on it, so it is stable and machine
readable. `message` is for humans and may change. `details` is present only when there is
something structured worth attaching. `request_id` is the same value as the
`X-Request-ID` response header, which every response carries; quote it in a bug report and
`logtool trace` will reconstruct the whole request.

### Error codes

| Code | HTTP | Meaning |
|---|---|---|
| `VALIDATION_ERROR` | 422 | The request body or query failed validation |
| `NOT_FOUND` | 404 | No such order, product or payment |
| `UNKNOWN_SKU` | 404 | A reservation named a SKU that does not exist |
| `OUT_OF_STOCK` | 409 | Not enough stock for at least one line |
| `CONFLICT` | 409 | Generic conflict, e.g. creating a product that already exists |
| `ILLEGAL_TRANSITION` | 409 | An order was asked to move to a status it cannot reach |
| `IDEMPOTENCY_KEY_REQUIRED` | 400 | `POST /orders` without the header |
| `IDEMPOTENCY_KEY_TOO_LONG` | 400 | The key exceeds the 64 characters the column holds |
| `IDEMPOTENCY_KEY_REUSED` | 409 | That key was used for a *different* order |
| `ORDER_IN_PROGRESS` | 409 | That key's order has not finished yet |
| `PAYMENT_IN_PROGRESS` | 409 | Another charge attempt for that order is still running |
| `PAYMENT_AMOUNT_MISMATCH` | 409 | A second charge for the same order named a different amount |
| `UPSTREAM_TIMEOUT` | 504 | A downstream service did not answer inside the timeout |
| `UPSTREAM_ERROR` | 502 | A downstream service failed. Its own code is in `details.upstream_code` |
| `DEPENDENCY_UNAVAILABLE` | 503 | The service cannot reach its database or Redis |
| `INTERNAL_ERROR` | 500 | An unhandled exception. Always a bug |

### What is *not* an error

Two outcomes look like failures and are deliberately returned as success:

- **A declined card** is `200` from payments with `status: FAILED`.
- **An order that could not be filled** is `201` from orders with `status: FAILED` and a
  `failure_reason`.

In both cases the request was understood and processed correctly; the business outcome was
negative. Returning 4xx would make the error-rate panel and the `HighServerErrorRate`
alert fire every time a customer's card is declined, and everyone would learn to ignore
them. Reasoning in [decisions.md](decisions.md#8-a-declined-payment-is-http-200).

---

## orders-service — :8001

### `POST /orders`

Header **`Idempotency-Key`** is required. Omitting it is `400 IDEMPOTENCY_KEY_REQUIRED`.

```json
{"customer_email": "kumar@example.com", "items": [{"sku": "SKU-0001", "qty": 2}]}
```

`items` holds 1 to 20 lines, `qty` is 1 to 100, and the same SKU may not appear twice —
inventory rejects a duplicated SKU, so it is caught at the edge rather than half way
through an order that has already been written.

Returns `201` with the order. `status` will be one of:

| Status | What happened |
|---|---|
| `CONFIRMED` | Stock reserved and committed, card charged, confirmation queued |
| `FAILED` | Reservation or payment failed. `failure_reason` says which, and any stock taken has been released |

There is a third outcome that is **not** a `201`. If inventory or payments times out or
returns a 5xx, the call fails with `504 UPSTREAM_TIMEOUT` or `502 UPSTREAM_ERROR` and the
order is deliberately left `PENDING` — not `FAILED`. A timeout is not an answer: the stock
may or may not have been taken, and releasing it on a guess is how you oversell. The order
sits there until `expire_stale_orders` releases it, within `ORDER_EXPIRY_MINUTES`.

### Re-sending the same key

Re-sending the same `Idempotency-Key` never charges twice, but what you get back depends
on the state of the original order:

| Original order | Response |
|---|---|
| Finished (`CONFIRMED`, `FAILED`, `EXPIRED`, `CANCELLED`) | `201` with that order, unchanged, no side effects |
| Still `PENDING` or `RESERVED` | `409 ORDER_IN_PROGRESS`, with the order id in `details` |
| Same key, **different** body | `409 IDEMPOTENCY_KEY_REUSED` |

The middle row matters. Returning `201 PENDING` would tell a client retrying after a
timeout that everything worked, when in fact it is holding a dead order. The last row
guards against a client generating colliding keys: the key is compared against the
`customer_email` and the sorted `(sku, qty)` lines, so a collision cannot hand one
customer another customer's order.

Two genuinely concurrent requests with the same key resolve on the unique index — one
inserts, the other reads the winner back.

### `GET /orders/{order_id}`

The order plus its `items` and its full `events` trail. The events are the useful part:
each row records `from_status`, `to_status`, `reason` and the `request_id` that caused the
change, so an order's history is readable long after the logs have gone.

### `GET /orders`

Query: `status`, `limit` (default 20, max 100), `offset`.

### `POST /orders/{order_id}/cancel`

Legal only from `CONFIRMED`. Returns the stock and moves the order to `CANCELLED`.
Anything else is `409 ILLEGAL_TRANSITION`.

### `GET /reports/daily?date=YYYY-MM-DD`

Counts and revenue for one **IST calendar day**, not a UTC day. The response includes the
`window_start` and `window_end` it actually used so the conversion is auditable:

```json
{
  "date": "2026-09-11",
  "timezone": "Asia/Kolkata",
  "window_start": "2026-09-10T18:30:00Z",
  "window_end": "2026-09-11T18:30:00Z",
  "orders": 412,
  "confirmed_orders": 389,
  "revenue_paise": 7743200
}
```

Only `CONFIRMED` orders contribute to `revenue_paise`. The window is half-open, so an
order at exactly midnight IST belongs to the day that is starting, not the one that ended.

---

## inventory-service — :8002

| Endpoint | Notes |
|---|---|
| `GET /products` | Cached in Redis for `PRODUCT_CACHE_TTL` seconds |
| `GET /products/{sku}` | Cached per SKU |
| `POST /products` | `201`. A duplicate SKU is `409 CONFLICT` |
| `PATCH /products/{sku}/stock` | Body `{"delta": -5}`. Invalidates the cache for that SKU |

### `POST /reservations`

```json
{"order_id": "…", "items": [{"sku": "SKU-0001", "qty": 2}, {"sku": "SKU-0002", "qty": 1}]}
```

**All or nothing.** Either every line is reserved and stock decremented, or nothing
changes at all. A partial reservation is never visible, even briefly.

- Unknown SKU → `404 UNKNOWN_SKU`
- Not enough stock on any line → `409 OUT_OF_STOCK`, naming the SKU, what is available and
  what was asked for
- The same SKU twice in one request → `422 VALIDATION_ERROR`
- **Calling it twice with the same `order_id` does not reserve twice** — the existing
  reservation is returned. orders-service retries after a timeout, and a retry must not
  take stock again.

Stock is decremented with a conditional `UPDATE ... WHERE stock >= qty`, and the lines are
processed in SKU order so two orders wanting the same two products cannot deadlock each
other. See [decisions.md](decisions.md#3-stock-is-decremented-with-a-conditional-update).

### `POST /reservations/{order_id}/commit`

Marks the reservation `COMMITTED`. A second call is a no-op; committing one that was
already released is `409 CONFLICT`.

### `POST /reservations/{order_id}/release`

Returns the stock and marks the rows `RELEASED`. **Idempotent, and deliberately forgiving:**
releasing an already-released reservation is a no-op, and so is releasing an `order_id`
that has no reservation at all. orders-service calls this as compensation on its failure
path, and compensation that can itself fail is worse than useless.

---

## payments-service — :8003

### `POST /payments`

```json
{"order_id": "…", "amount_paise": 39800}
```

Behaviour depends entirely on what already exists for that `order_id` — this service's
whole job is charging an order at most once:

| Existing state | Result |
|---|---|
| Nothing | Insert `PENDING`, call the gateway, record the outcome |
| `SUCCEEDED` | Return it unchanged. **The gateway is not called again** |
| `PENDING` | `409 PAYMENT_IN_PROGRESS` — another attempt is in flight |
| `FAILED` | Retry is allowed; `attempts` increments |
| Different `amount_paise` | `409 PAYMENT_AMOUNT_MISMATCH` — refusing beats guessing |

A `PENDING` row that is older than a few minutes means we asked the gateway and never got
an answer. Whether the customer was charged is then genuinely unknown, and it takes a
human to reconcile — see [decisions.md](decisions.md#9-open-question--the-payment-timed-out-but-the-customer-was-charged).

### `GET /payments/{order_id}`

`404 NOT_FOUND` when there is no payment for that order.

### The simulated gateway

Not an endpoint — it is the stand-in for a card processor, driven by environment
variables, and it is how most of the interesting failures get produced:

| Variable | Effect |
|---|---|
| `GATEWAY_LATENCY_MS_MIN` / `_MAX` | Random latency on every call |
| `GATEWAY_FAILURE_RATE` | Probability of a decline (default 0.05) |
| `GATEWAY_TIMEOUT_RATE` | Probability of hanging for `GATEWAY_TIMEOUT_SLEEP_S`, which is longer than the caller's timeout |
| `GATEWAY_SEED` | Fixes the random sequence so behaviour is reproducible |

---

## support-service — :8004

Read-only. It owns no data: it reads the shared log volume, calls the other services'
`/ready` endpoints, and reads `incidents/` off disk. Same error envelope and same
`X-Request-ID` header as everything else.

Through the console's nginx these are reachable at `/api/support/...` on port 5173.

### `GET /support/overview`

Health of every service in one call, probed in parallel, plus a count of recent errors.

```json
{
  "generated_at": "2026-09-12T08:37:02Z",
  "services": [
    {"name": "orders", "url": "http://orders:8001", "live": true, "ready": true,
     "detail": null, "latency_ms": 13.0, "checks": {"database": "ok", "broker": "ok"}}
  ],
  "errors_last_15m": 487,
  "log_lines_read": 17719,
  "malformed_lines": 0
}
```

`live` and `ready` are deliberately separate. A service that answers `/ready` with a 503 is
`live: true, ready: false` — the process is up and refusing traffic, which is a different
problem from a process that is gone, and needs a different response.

### `GET /support/trace/{request_id}`

Every log line carrying that request id, across every service, in time order.

```json
{
  "request_id": "6a21bb73aa6e463db64ad20f34c818cd",
  "services": ["inventory", "orders", "payments", "worker"],
  "span_ms": 1732.73,
  "order_ids": ["680ad39d-3511-49b7-b054-429471935fc4"],
  "steps": [
    {"timestamp": "2026-09-12T08:37:11.358Z", "service": "payments",
     "level": "info", "event": "payment_settled", "gap_ms": 857.52,
     "duration_ms": null, "status_code": null, "order_id": "680ad39d-...",
     "fields": {"provider_ref": "PAY-556b5b172f56"}}
  ]
}
```

`gap_ms` is the point of the endpoint: milliseconds since the previous line. A request that
took two seconds did not spend them evenly, and the largest gap names the step that owns
the problem.

Fields the response models name — service, level, event, `request_id`, `duration_ms`,
`status_code`, `order_id` — are lifted out. Everything else the service chose to log for
that event stays in `fields` verbatim, because the interesting key is different every time
and a fixed schema would throw it away.

404 `NOT_FOUND` if no line carries that id.

### `GET /support/errors?since=15m`

Warning-and-above lines grouped by `(service, error_code)`, most frequent first, each with
its most recent example. `since` accepts `30s`, `15m`, `2h`, `1d`; anything else is a 422
`VALIDATION_ERROR`.

A line with no explicit `error_code` is grouped under its `event` name — unless that event
is prose from a third-party library, in which case it is bucketed as `UNSTRUCTURED`. Celery
writes whole sentences into `event` and varies the retry delay inside them, so using them
as keys gives one group per line and a useless board.

### `GET /support/slow?since=15m&top=10`

The slowest completed requests in a window, by the `duration_ms` on their access-log line.
Only `http_request` events are candidates — a slow cache read is not a slow request.

### `GET /support/order/{order_id}`

Every log line that mentions an order id anywhere, including nested inside a `details`
object, which is where it usually hides on the failure paths. Returns the lines plus the
distinct `request_ids` involved — more than one usually means a retry, or the expiry job
arriving while the order was still in flight. 404 if nothing mentions it.

### `GET /support/incidents`

The Phase 3 board: the public half of each fault definition, joined to any row already
written into `incidents/INDEX.md`. It never reads the sealed `spoiler` field — the console
is something you look at *during* an investigation.
