# PROGRESS

## Current position

Phase: 3 | Next task: **INC-003** (`python tools/chaos.py start INC-003`)

Phases 1 and 2 are built and verified on live infrastructure. Phase 3 has two of twelve
incidents closed, both fully worked with real evidence off the running stack. The remaining
ten are written and sealed, waiting to be worked one at a time. A storefront and ops console
have been added on top, plus a fifth service that serves the log tooling over HTTP.

## Environment

| Tool | Status |
|---|---|
| Python 3.12 | installed (3.12.10 as `py -3.12`; the project venv is 3.12.14) |
| uv | installed (0.12.10) |
| git | installed (2.45.1) — repo initialised, `core.hooksPath` set to `.githooks` |
| Docker Desktop | installed — engine 29.7.2, compose v5.5.1; eleven containers build and run |
| Node.js / npm | installed (22.22.0 / 11.16.0) — needed for the frontend only |

Host venv is `.venv` (`uv venv .venv --python 3.12`, then
`uv pip install -r requirements-dev.txt`). All four services and `common` are installed
editable, so ruff and pytest run from the repo root with no containers. Install a new
service with `uv pip install -p .venv -e services/<name>` — note this venv has no `pip`,
only `uv`.

## Test suite

**326 passing, 3 skipped, ruff clean across 105 files.** Run it with
`.venv\Scripts\python.exe run_tests.py` (add `--cov` for the coverage column).

| Component | Tests | Coverage |
|---|---|---|
| `common` | 38 | 81% |
| `inventory` | 45 (+1 skipped) | 96% |
| `payments` | 26 (+1 skipped) | 94% |
| `orders` | 145 (+1 skipped) | 98%, **`state.py` 100%** |
| `support` | 16 | 95% |
| `tools` | 50 | — |
| `tests` (repo-level) | 6 | — |

The frontend has no unit tests — it typechecks under `tsc --noEmit` with `strict` and
`noUncheckedIndexedAccess`, and is exercised end to end against the live stack. That is a
real gap, listed under open questions.

The three skips are the PostgreSQL-only concurrency tests, one per service. They need
genuinely concurrent transactions against the same row, and SQLite takes a database-wide
write lock that would pass them even against code that is wrong. Point `TEST_DATABASE_URL`
at PostgreSQL to run them.

`run_tests.py` exists because all four services install a top-level package named `app`;
one pytest process can only hold one of them, so each suite runs in its own subprocess.

## What was added this session

**The failing concurrency test is fixed.** `services/orders/tests/test_concurrency.py` was
asserting pre-fix behaviour — that both racing requests come back with an order id. The
service deliberately answers the loser with `409 ORDER_IN_PROGRESS`, the same thing Stripe
does for a key already in use. The test now gates the winner inside the reservation call so
the race is deterministic, and asserts what the service actually guarantees: one order row,
one reserve, one charge, one 201, one 409.

**The repo is a git repo.** Initialised, three feature commits plus two incident merges.

**support-service (`:8004`)** — a fourth FastAPI service that owns nothing: no database, no
migrations, no writes. It mounts the log volume and `incidents/` read-only, probes the other
services' `/ready` endpoints in parallel, and serves the four `logtool` questions over HTTP.
The log-analysis core moved to `common/logsearch.py` so the CLI and the API cannot drift.

**The web app (`:5173`)** — React + TypeScript + Vite, built and served by nginx, which also
proxies `/api/*` so no Python service needs CORS. Two halves: a storefront that exercises
the real order flow including the idempotency key, and an ops console with service health,
an error and slow-request board, the order queue with a stuck-order banner, an order's state
machine beside its log lines, a request-id trace viewer, and the incident board.

**The Phase 3 harness (`tools/chaos.py`)** and twelve sealed fault definitions covering all
twelve categories in CLAUDE.md §8.2.

**INC-001 and INC-002 closed**, both with evidence measured off the running stack.

## Verification — this session, on live infrastructure

| Check | Result |
|---|---|
| Eleven containers up and healthy | pass |
| Order end to end through the nginx proxy | pass — `PENDING→RESERVED→CONFIRMED` |
| Trace across four services | pass — 17 lines, span 1.73s, longest gap 858ms at the payment gateway |
| Ops console against live data | pass — all four screens, 899 orders, ₹47.36L, 3/3 services ready |
| INC-001 reproduced and mitigated | pass — 2.1% checkout failure under load, 0 after |
| INC-001 stuck orders cleared by the beat job | pass — 9 EXPIRED at T+15, 38 units returned, 0 ACTIVE reservations |
| INC-001 guard works in a real container | pass — `INVENTORY_TIMEOUT_S=0.25` now refuses to boot |
| INC-002 reproduced and quantified | pass — 12 SKUs, 720 units, ₹17,85,600 hidden, 0 errors logged |
| INC-002 mitigated and verified | pass — a restock is visible on the very next read; 0 of 50 SKUs disagree |

## Python focus areas (from M0 diagnostic)

**Still not done.** The eight-exercise diagnostic in CLAUDE.md §7 — comprehensions,
dataclasses, custom exceptions, type hints, context managers, decorators, async/await and
reading a traceback — has not been worked through. Worth doing before INC-003, because
Phase 3 is the part that gets defended in an interview.

## Milestones

- [x] M0 Environment **(Python diagnostic still outstanding)**
- [x] M1 inventory-service
- [x] M2 payments-service
- [x] M3 orders-service + orchestration
- [x] M4 Redis cache + Celery
- [x] M5 Docker Compose full stack ← Phase 1
- [x] M6 Logging + request tracing
- [x] M7 Metrics + dashboard + alerts
- [x] M8 Support toolkit ← Phase 2
- [x] Web: storefront + ops console, and support-service behind it
- [x] Phase 3 harness + twelve sealed faults
- [ ] INC-001 … INC-012 — **2 of 12 closed**
- [ ] Final polish (demo script, interview prep, CV bullets)

## Decisions made (one line each, details in docs/decisions.md)

- Three databases in one PostgreSQL instance, no cross-database foreign keys.
- Synchronous SQLAlchemy and plain `def` endpoints, so Celery tasks and HTTP handlers share one code path.
- Stock is decremented with a conditional `UPDATE ... WHERE stock >= qty`, not read-modify-write.
- Reservation lines are processed in SKU order, which removes the deadlock between two orders wanting the same pair.
- One installed `common` package, so the services cannot drift.
- The unhandled-500 handler is middleware, so the error body keeps its request id.
- Tests default to throwaway SQLite; only the locking tests need PostgreSQL.
- The Celery worker runs a thread pool and serves Prometheus on 9100.
- A declined card is HTTP 200 with `status: FAILED`; business outcomes are not transport errors.
- Cache failures degrade to a database read and never propagate.
- A reservation *timeout* leaves the order PENDING, because we cannot tell whether stock was taken.
- **support-service owns nothing and writes nothing** — read-only mounts make it a guarantee.
- **One origin for the browser**, so no service needs CORS.
- **Phase 3 faults are sealed, not hidden** — base64, and `reveal` is gated on a written RCA.

## Open questions / blockers

- **The frontend has no automated tests.** It typechecks and was driven by hand against the
  live stack, which is not the same thing. A couple of Vitest tests around the cart's
  idempotency-key handling and the trace gap calculation would be the highest value.
- **No CI.** INC-002 is the argument for it: a change that a *passing* test would have
  rejected reached a running environment because nobody ran the suite. `.githooks/pre-push`
  is the stopgap and is skippable with `--no-verify`.
- **No consistency check comparing the API against the database.** INC-002's prevention item
  3, still outstanding. It is the signal that would turn "a category manager noticed after
  two days" into an alert.
- **`GET /products` returns the whole catalogue and takes no `limit`.** FastAPI ignores
  undeclared query parameters, so `?limit=3` silently returns all 50. Harmless at this size.
- **support-service reads every log line on every request.** Fine at 17k lines and ~0.5s;
  it will not be at 500k. `SUPPORT_MAX_RECORDS` trims *after* reading, which does not help.
  Reading the tail of each file is the fix when it starts to hurt.
- **`CLAUDE.md` names an AI assistant.** Several files reference it. Worth renaming before
  the repo is shown to anyone, though doing so stops it working as assistant instructions.
- **M3's open question** — payment timed out but the gateway charged the customer — is
  written up as decision 9, and the reconciliation job it argues for is still not built.
  INC-004 is about exactly this, which is a good reason to leave it until then.
- **No end-to-end test driving the live stack from pytest.** Covered by hand in
  `docs/verification.md`.

## Session log

| Date | What was done | Next |
|---|---|---|
| 2026-09-12 (2) | Fixed the orders concurrency test. `git init`. Built support-service and the React storefront + ops console, both verified against live data. Built the Phase 3 chaos harness and twelve sealed faults covering all twelve categories. Worked INC-001 and INC-002 end to end with real measurements — including two honest corrections: INC-001's mechanism is client-side queueing, not a slow dependency, and INC-002's "missing" regression test already existed and already caught the bug, which moved the root cause to the absent test gate. Added `UpstreamTimeouts`, `upstream_calls_total`, two config guards, two runbooks and a pre-push hook. | INC-003 |
| 2026-09-12 (1) | Docker installed. Ran `docs/verification.md` steps 0–7 on the live stack for the first time: M5 traffic run 100% clean, zero orphaned reservations, `ServiceDown` fired at exactly 60s, compensation verified by killing payments mid-flight, 444/444 notifications. | Rewrite `test_concurrency.py`, `git init`, then INC-001 |
| 2026-09-11/12 | Built Phases 1 and 2 end to end. 289 tests, ruff clean. Thirteen defects found and fixed during review. | Install Docker, work through `docs/verification.md` |

## Bugs found and fixed while building

Each has a regression test. (1) Unhandled 500s came back with a null request id — FastAPI's
`Exception` handler runs outside our middleware. (2) Error responses carried `X-Request-ID`
twice. (3) `get_session` documented a rollback it never performed. (4) `ruff.toml` used
`exclude` instead of `extend-exclude` and walked into `.venv`. (5) A transaction, and its
pooled connection, was held open across every outbound HTTP call in `POST /orders`.
(6) A replayed idempotency key for a still-PENDING order returned 201. (7) Idempotency keys
were never checked against the request body. (8) `expire_stale_orders` moved orders to the
terminal EXPIRED even when the release had failed. (9) Two overlapping expiry runs wrote
duplicate `order_events`. (10) `orders_total` was incremented before the commit.
(11) `list_orders` paged on a non-unique `created_at`. (12) An upstream 5xx was re-raised
carrying the upstream's own error code. (13) `test_state.py` generated its expectations from
the table it was testing.

Found this session, by running the thing rather than reading it: (14) support-service tried
to write its own log into the volume it mounts read-only — it logs to stdout instead, which
is also the right design. (15) The health probe called three `/ready` endpoints serially, so
the screen you open to find out what is down was the slowest thing on it. (16) Celery writes
prose into the log's `event` field with a varying retry delay inside it, so every line became
its own error group and the board was unreadable. (17) nginx resolved each upstream name
once at startup and cached the address for the life of the process, so rebuilding any
backend container left the whole console 502ing against a dead IP until nginx was restarted
too — which on this stack is a daily event, since every Phase 3 incident restarts a service.
The upstreams now go through a `resolver` and a variable, so they are re-resolved per
request. Proved by making inventory move from `172.18.0.3` to `172.18.0.13` with nginx
untouched: still 200.
