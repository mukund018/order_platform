# PROGRESS

## Current position

Phase: 3 | Milestone: INC-001 not started | Next task: fix
`services/orders/tests/test_concurrency.py` (see "Outstanding for Kumar" below), then
`git init`, then INC-001.

Phases 1 and 2 were built in one pass rather than milestone by milestone, so the list
below is ticked against the acceptance criteria in the project plan, not against the
order the work actually happened in.

**2026-09-12: Docker installed, stack run for the first time, `docs/verification.md`
steps 0–7 executed.** Everything passes except one test, which turned out to be asserting
the wrong thing. Recorded numbers are in the verification section below.

## Environment

| Tool | Status |
|---|---|
| Python 3.12 | installed (3.12.10 as `py -3.12`; the project venv is 3.12.14) |
| uv | installed (0.12.10) |
| git | installed (2.45.1) — **repo still not initialised** (blocks Phase 3 branches) |
| Docker Desktop | installed — engine 29.7.2, compose v5.5.1; full stack builds and runs |

Host venv is `.venv`, built with `uv venv .venv --python 3.12` then
`uv pip install -r requirements-dev.txt`. It has `common` and all three services installed
editable, so ruff and pytest run from the repo root with no containers.

## Test suite

289 passing, 3 skipped, ruff clean across 86 files. Run it with
`.venv\Scripts\python.exe run_tests.py` (add `--cov` for the coverage column).

| Component | Tests | Coverage |
|---|---|---|
| `common` | 32 | — |
| `inventory` | 40 (+1 skipped) | 96%, `service.py` 99% |
| `payments` | 26 (+1 skipped) | 94%, `gateway.py` 98% |
| `orders` | 135 (+1 skipped) | 98%, **`state.py` 100%**, `tasks.py` 100% |
| `tools` | 50 | — |
| `tests` (repo-level) | 6 | — |

`run_tests.py` exists because all three services install a top-level package named `app`;
one pytest process can only hold one of them, so each suite runs in its own subprocess.

The three skips are one `test_concurrency.py` per service. They are marked
`@pytest.mark.db` because each needs genuinely concurrent transactions against the same
row, and SQLite takes a database-wide write lock — it would pass them even against code
that is wrong. They run with `TEST_DATABASE_URL` pointed at PostgreSQL:

- inventory: 20 threads reserving 1 unit against 10 in stock, exactly 10 win, stock hits 0
- payments: concurrent charges for one order must not double-charge
- orders: concurrent requests with the same `Idempotency-Key` must produce one order

Target was ≥ 70% per service with the state machine near 100%. Both met.

## Python focus areas (from M0 diagnostic)

**Not done.** The M0 diagnostic from the project plan — eight exercises on
comprehensions, dataclasses, custom exceptions, type hints, context managers, decorators,
async/await and reading a traceback — has not been worked through. Worth doing before
Phase 3, since Phase 3 is the part that gets defended in an interview.

## Verification run — 2026-09-12 (first time on live infrastructure)

Every number here came off the running stack, not a mock.

| Step | Criterion | Result |
|---|---|---|
| 1 | Nine containers up, `/ready` reaches Postgres and Redis | pass |
| 2 | Seed 50 products | pass — `--seed 7`, reproducible |
| 3 | One order end to end; same key twice creates one order | pass — `PENDING→RESERVED→CONFIRMED`, one order row and three event rows after three identical POSTs, stock 19→17 exactly once |
| 4 | M5: 10 rps for 120 s, ≥ 95% success | **pass — 100%.** 1200 requests, 0 server errors, 0 transport errors, p50 21 ms / p95 409 ms / p99 1634 ms |
| 4 | Consistent data across three databases | **pass — zero `ACTIVE` reservations.** 817 COMMITTED, 47 RELEASED, no negative stock, no order stuck PENDING or RESERVED |
| 5 | M6: follow one request id across services | pass — all four `logtool` subcommands |
| 6 | M1: 20 threads, 10 units, never oversell | pass on PostgreSQL |
| 6 | M2: concurrent charges never double-charge | pass on PostgreSQL |
| 6 | M3: concurrent duplicate keys create one order | **test fails — the test is wrong, not the code.** See below |
| 7 | M7: dashboard provisioned, alert fires | pass — 13 panels, 5 rules, `ServiceDown` pending at 60 s and firing at 61 s, resolved 60 s after restart |
| 7 | Compensation when a dependency dies | pass — payments stopped, order went `RESERVED→FAILED` with `payments is unreachable`, reservation `RELEASED`, no stock stranded |
| 8 | M8: debugger attach | partial — debugpy listens on 5678 and the service keeps serving; hitting a breakpoint in VS Code still untried |
| — | Celery under load | pass — 444 CONFIRMED orders, exactly 444 notification rows, beat firing every 60 s |
| — | IST daily report | pass — window `18:30Z → 18:30Z`, 700 orders, 444 confirmed, ₹41,41,127 revenue |

## Outstanding for Kumar

**1. `services/orders/tests/test_concurrency.py` fails on PostgreSQL.** It has never run
before — SQLite skipped it — so this contradiction has been sitting there since bug #6 was
fixed.

Two requests race with the same `Idempotency-Key`. One wins the unique-index insert and
runs the flow; the loser calls `_replay`, finds the order still `RESERVED`, and raises
`ORDER_IN_PROGRESS` (409). Confirmed live against the running stack:

```
409  ORDER_IN_PROGRESS  order 2a705442-… placed with this key is still PENDING
201  order 2a705442-…  status=CONFIRMED
```

One order row, one reservation, one charge. That is the behaviour fix #6 deliberately
introduced, and it is what Stripe does (`idempotency_key_in_use`). The test still asserts
`len(order_ids) == 1` — that *both* threads come back with an order id — which was the
pre-fix behaviour.

So the test needs rewriting to assert what the service actually guarantees: one order row,
one `reserve` call, one `charge` call, one winner with a 201, one loser with a
`ConflictError` carrying `ORDER_IN_PROGRESS`. Kumar writes it. Worth
thinking about first: *is* 409 the right answer for a client that simply retried after a
network blip, and what would the alternative cost?

**2. The repo is still not a git repo.** Phase 3 injects faults on `incident/INC-00X`
branches, so `git init` and a first commit have to happen before INC-001.

**3. p99 was 1634 ms under a 10 rps load.** `logtool trace` on the one timeout in the
first run shows orders giving up at 2005 ms on `GET /products/SKU-0013` while inventory
logged that same request as `200` in 112 ms — and logged it *after* orders had already
given up. So inventory was not slow; the request sat ~2 s before inventory started it.
Queueing somewhere. A real, naturally occurring symptom with real evidence already
captured — a good candidate for an investigation, or for a Phase 3 incident built on top
of whatever is actually causing it.

## Milestones

- [x] M0 Environment (Docker still missing) + Python baseline **(diagnostic outstanding)**
- [x] M1 inventory-service
- [x] M2 payments-service
- [x] M3 orders-service + orchestration
- [x] M4 Redis cache + Celery
- [x] M5 Docker Compose full stack ← Phase 1 *(compose written, never run)*
- [x] M6 Logging + request tracing
- [x] M7 Metrics + dashboard + alerts *(rules written, never observed firing)*
- [x] M8 Support toolkit ← Phase 2
- [ ] INC-001 … INC-012 ← Phase 3
- [ ] Final polish

## Decisions made (one line each, details in docs/decisions.md)

- Three databases in one PostgreSQL instance, no cross-database foreign keys.
- Synchronous SQLAlchemy and plain `def` endpoints, so Celery tasks and HTTP handlers share one code path.
- Stock is decremented with a conditional `UPDATE ... WHERE stock >= qty`, not read-modify-write.
- Reservation lines are processed in SKU order, which removes the deadlock between two orders wanting the same pair.
- One installed `common` package for logging, request ids, errors, health and metrics, so the three services cannot drift.
- The unhandled-500 handler is middleware, not `@app.exception_handler(Exception)`, so the error body keeps its request id.
- Tests default to throwaway SQLite; only the locking tests need PostgreSQL.
- The Celery worker runs a thread pool and serves Prometheus on 9100, because prefork means one registry per child.
- A declined card is HTTP 200 with `status: FAILED`; business outcomes are not transport errors.
- Cache failures degrade to a database read and never propagate.
- A reservation *timeout* leaves the order PENDING rather than FAILED, because we cannot tell whether stock was taken.

## Bugs found and fixed while building

Each has a regression test.

1. Unhandled 500s came back with `"request_id": null`. FastAPI installs an `Exception`
   handler on Starlette's `ServerErrorMiddleware`, which runs *outside* our middleware, so
   the id had already been cleared. Replaced with `UnhandledErrorMiddleware`.
2. Error responses carried `X-Request-ID` twice — both `error_response()` and the
   middleware were setting it. The middleware is now the sole owner.
3. `get_session` documented a rollback it never performed. A multi-item reservation that
   failed on its second line kept the stock it had already taken from the first.
4. `ruff.toml` used `exclude` instead of `extend-exclude`, which replaced ruff's built-in
   ignore list and sent `ruff check .` walking into `.venv` — 45,994 findings.
5. A database transaction, and its pooled connection, was held open across every outbound
   HTTP call in `POST /orders` and in cancel. With a pool of 5+5 that is how the service
   runs out of connections the moment inventory gets slow. Verified with
   `engine.pool.checkedout()` inside the mocked calls: was 1, now 0.
6. A replayed `Idempotency-Key` whose order was still `PENDING` returned `201`, telling a
   client that had timed out that its order was fine. Now `409 ORDER_IN_PROGRESS`.
7. Idempotency keys were never checked against the request, so a client-side key collision
   would hand one customer another customer's order.
8. `expire_stale_orders` moved an order to the terminal `EXPIRED` even when the
   compensating release had failed, stranding that stock permanently.
9. Two overlapping expiry runs both expired the same order and wrote duplicate
   `order_events` rows. Now `FOR UPDATE SKIP LOCKED` per order.
10. `orders_total` was incremented before the commit, so any rolled-back path
    permanently over-counted the metric.
11. `list_orders` paged on `created_at`, which is not unique — rows could repeat or be
    skipped between pages.
12. An upstream 5xx was re-raised carrying the upstream's own error code, so orders
    answered a broken inventory with `INTERNAL_ERROR` instead of `UPSTREAM_ERROR`.
13. `test_state.py` generated its expectations from `ALLOWED`, the table it was testing,
    so a corrupted state table would still have passed. It now asserts against a literal
    table written from the spec.

## Open questions / blockers

- ~~Docker Desktop is not installed~~ — done 2026-09-12, stack verified, see above.
- **The git repo is not initialised.** Phase 3 needs it — incidents get injected on
  `incident/INC-00X` branches.
- **`GET /products` returns the whole catalogue and takes no `limit`.** FastAPI ignores
  undeclared query parameters, so `?limit=3` silently returns all 50 instead of failing.
  Harmless at this size; a decision to make before the catalogue grows.
- M3's open question — payment timed out but the gateway charged the customer — is written
  up as decision 9, but the reconciliation job it argues for is not built. Deliberate gap,
  and a good thing to be asked about.
- There is no end-to-end test driving the live stack from pytest. The traffic run plus the
  consistency queries in `docs/verification.md` step 4 cover it by hand for now.

## Session log

| Date | What was done | Next |
|---|---|---|
| 2026-09-12 | Docker installed. Ran `docs/verification.md` steps 0–7 on the live stack for the first time: M5 traffic run 100% clean (1200 requests, 0 server errors), zero orphaned reservations, `ServiceDown` fired at exactly 60s, compensation verified by killing payments mid-flight, 444/444 notifications. Found three things: the orders concurrency test asserts pre-fix behaviour and fails on PostgreSQL; verification step 6 as written would have dropped the live schema; `?limit=` on `/products` is silently ignored. Fixed the doc and `init.sql`; left the test for Kumar. | Rewrite `test_concurrency.py`, `git init`, then INC-001 |
| 2026-09-11/12 | Built Phases 1 and 2 end to end: `common`, the three services, Celery worker and beat, Redis product cache, compose stack, Prometheus + Grafana + 5 alert rules, the three CLI tools, 7 runbooks and the docs. 289 tests, ruff clean, coverage 94–98%. Thirteen defects found and fixed during review, listed above — the connection held across outbound calls and the terminal-EXPIRED-without-release one were the two that mattered most. | Install Docker, work through `docs/verification.md` steps 1–8 |
