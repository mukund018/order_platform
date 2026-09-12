# Order Platform

A small order-processing system built the way a real one is built, so that it can fail the
way a real one fails — and then be diagnosed.

Three Python services that depend on each other, a background worker, a database per
service, a cache, a simulated payment gateway with latency and failure knobs, structured
logs with request tracing, Prometheus metrics, alert rules, a log-analysis CLI, and a web
app that is both the shop and the support console.

It exists to practise **application support**: reading logs, following one request across
four processes, telling a slow dependency apart from a broken one, and writing down what
happened afterwards. Phase 3 adds twelve injected production faults to practise on.

---

## What it does

A customer places an order. The orders service prices it against inventory, reserves the
stock, charges the card, and confirms — and every one of those steps can fail on its own.

```
POST /orders  ─┬─▶ inventory: price the lines
               ├─▶ orders_db: insert PENDING, commit
               ├─▶ inventory: reserve stock (all or nothing)  ──fails──▶ FAILED
               ├─▶ payments:  charge the card                 ──fails──▶ release stock, FAILED
               └─▶ inventory: commit reservation, CONFIRMED, queue the confirmation email
```

The interesting part is the failure paths. There is no transaction spanning three
databases, so when the payment declines, the reservation has to be undone by a second
call — and if *that* fails, stock is stranded and the system says so loudly instead of
pretending otherwise.

See [docs/architecture.md](docs/architecture.md) for the diagrams and
[docs/decisions.md](docs/decisions.md) for why it is built this way.

---

## Running it

```powershell
Copy-Item .env.example .env
docker compose up -d --build
.venv\Scripts\python.exe tools\seed.py
```

| | |
|---|---|
| **Shop and ops console** | **http://localhost:5173** |
| orders | http://localhost:8001/docs |
| inventory | http://localhost:8002/docs |
| payments | http://localhost:8003/docs |
| support | http://localhost:8004/docs |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3000 |

Place an order:

```powershell
curl.exe -s -X POST http://localhost:8001/orders `
  -H "Content-Type: application/json" `
  -H "Idempotency-Key: demo-1" `
  -d '{\"customer_email\":\"demo@example.com\",\"items\":[{\"sku\":\"SKU-0001\",\"qty\":2}]}'
```

Send it twice — the second one returns the same order and changes nothing.

Then put some load through it:

```powershell
.venv\Scripts\python.exe tools\traffic.py --rps 10 --duration 120
```

---

## The console

`http://localhost:5173` is one React app with two halves, on one origin. nginx serves the
build and proxies `/api/*` to the services, which is why no Python service in this repo has
a line of CORS configuration — there is no cross-origin request to allow.

**The shop** browses the catalogue and places real orders through the real flow, including
the `Idempotency-Key` header, so a double-clicked button cannot buy the same cart twice. A
declined card comes back as HTTP 201 with a `FAILED` order, not as an error — a business
outcome is not a transport failure — and the checkout screen has to read `status` to know
what happened. Orders then poll until they reach a state they can never leave.

**The ops console** is the half that makes this an application-support project:

| Screen | What it answers |
|---|---|
| Overview | Is anything down, what is erroring, what is slow — the three questions in the order you ask them |
| Orders | Where is this customer's order, and which orders are stuck past the expiry window |
| Order detail | The state machine next to every log line that mentions that order, from every service |
| Trace | Paste a request id: every log line carrying it, across all four services, with the gap between each step |
| Incidents | The Phase 3 board — status, severity, time to mitigate, RCA score |

The trace screen is the one worth demoing. It names the bottleneck for you: *longest gap
858ms, before payments/payment_settled*. That number is why the request-id middleware
exists.

Behind it is **support-service** (`:8004`), a fourth FastAPI service that owns nothing. No
database, no migrations, no writes — it mounts the log volume read-only, calls the other
services' own `/ready` endpoints, and serves the four `logtool` questions over HTTP. Both it
and the CLI call the same functions in `common/logsearch.py`, so the console and the
terminal can never disagree about what counts as an error.

---

## The support toolkit

This is the part the project is actually about.

**Follow one request across every service.** Every response carries an `X-Request-ID`;
every log line in every process carries it too, including the Celery worker.

```powershell
.venv\Scripts\python.exe tools\logtool.py trace b17c4e2a9f0d4c1e8a3b5d7f2e6c9a04
```

```
request_id b17c4e2a9f0d4c1e8a3b5d7f2e6c9a04  9 lines across 3 services  span 790ms
18:19:34.968             orders     info     order_created         order_id=3f6b... total_paise=39800
18:19:35.008      +40ms  inventory  info     stock_reserved        order_id=3f6b... items=2
18:19:35.058      +35ms  payments   info     gateway_call          order_id=3f6b... latency_ms=612
18:19:35.678     +620ms  payments   warning  payment_declined      error_code=CARD_DECLINED
18:19:35.728      +45ms  inventory  info     reservation_released  units=2
18:19:35.748      +20ms  orders     warning  order_failed          error_code=PAYMENT_DECLINED
```

The `+620ms` is the answer to "why was that order slow", and the `reservation_released`
line is the proof that the compensation actually ran.

The other three subcommands:

```powershell
.venv\Scripts\python.exe tools\logtool.py errors --since 15m     # grouped by service and code
.venv\Scripts\python.exe tools\logtool.py slow --top 10          # slowest requests
.venv\Scripts\python.exe tools\logtool.py order <order-id>       # one order's whole story
```

[docs/support-toolkit.md](docs/support-toolkit.md) maps symptoms to the tool to reach for
first — `pg_stat_statements`, `EXPLAIN ANALYZE`, `pg_stat_activity`, `redis-cli`,
`py-spy`, `tracemalloc`, and the debugger.

---

## Monitoring

Prometheus scrapes all three services plus the worker; Grafana is provisioned
automatically with a dashboard covering request rate, error rate, p95 latency, orders by
status, payment outcomes, Celery task results and database pool usage.

Six alert rules in [monitoring/alerts.yml](monitoring/alerts.yml), each linked to a
runbook in [runbooks/](runbooks/):

| Alert | Fires when |
|---|---|
| `ServiceDown` | a service has not been scraped for 1 minute |
| `HighServerErrorRate` | 5xx above 5% for 2 minutes |
| `HighLatencyP95` | p95 above 1s for 5 minutes |
| `UpstreamTimeouts` | one service is abandoning calls to another — **added because of INC-001** |
| `OrdersStuckPending` | more than 10 orders unfinished for 5 minutes |
| `CeleryTaskFailureRate` | task failures above 10% |

`OrdersStuckPending` is the one worth pointing at: it can fire while every request returns
200 and every latency panel looks normal. Orders quietly stop completing and money stops
arriving. That failure is invisible to anything that only watches HTTP status codes.

Debugging inside a running container works too — set `DEBUG_ATTACH=1` and attach with the
VS Code configurations in [.vscode/launch.json](.vscode/launch.json), worker included.

---

## Stack

Python 3.12 · FastAPI · SQLAlchemy 2.0 (sync) · Alembic · psycopg 3 · PostgreSQL 16 ·
Redis 7 · Celery 5 · structlog · prometheus-client · Prometheus · Grafana · pytest · ruff ·
Docker Compose

Money is integer paise, never floats. Timestamps are `timestamptz` in UTC, converted to
the IST business day only at the reporting boundary.

---

## Layout

```
common/       logging, request ids, error envelope, health, metrics, log search — installed into every service
services/     inventory (:8002), payments (:8003), orders (:8001) + worker and beat, support (:8004)
frontend/     react + typescript spa, served by nginx, proxies /api/* to the services
tools/        seed.py, traffic.py, logtool.py, chaos.py
monitoring/   prometheus config, alert rules, provisioned grafana dashboard
runbooks/     one per alert, plus the failures that do not have an alert yet
docs/         architecture, api, decisions, support toolkit, verification
incidents/    phase 3 — sealed fault definitions, tickets, investigations, RCAs
tests/        repo-level checks: migrations match models, services boot from .env.example
```

---

## Tests

```powershell
.venv\Scripts\python.exe run_tests.py
```

```
suite         passed  failed  skipped   cov
-------------------------------------------
common            38       0        0   81%
inventory         40       0        1   96%
payments          26       0        1   94%
orders           145       0        1   98%
support           16       0        0   95%
tools             50       0        0   66%
repo               6       0        0   20%
-------------------------------------------
total            321       0        3
```

The frontend typechecks and builds as part of its own container: `npm run typecheck`,
`npm run build`.

Each suite runs in its own subprocess, because all three services install a top-level
package called `app` and one pytest process can only have one of them. Add `--cov` for
coverage — the order state machine is at 100% — or name suites to run a subset.

The suite runs on SQLite by default so it needs no server. The three skips are the
concurrency tests, one per service — they need real PostgreSQL because SQLite takes a
database-wide write lock and would pass them even against code that is wrong. Point
`TEST_DATABASE_URL` at PostgreSQL and they run, including the oversell test: 20 threads
reserving against 10 units, asserting exactly 10 succeed and stock never goes negative.

[docs/verification.md](docs/verification.md) is the full checklist, with the command and
the expected result for every acceptance criterion.

---

## Status

Phases 1 and 2 are built. Phase 3 — twelve injected production incidents, each triaged,
investigated, escalated, fixed and written up as an RCA — is the next piece of work, and
is what [incidents/](incidents/) and the templates in it are for.

Two things are honestly not yet verified, because Docker is not installed on the machine
this was written on: the live end-to-end traffic run, and watching an alert actually fire.
Both are step-by-step in [docs/verification.md](docs/verification.md), and
[PROGRESS.md](PROGRESS.md) tracks what is done and what is not.
