# Verification

Every acceptance criterion from CLAUDE.md section 7, as a command you can run and a
result you can check. Work down the list; each step assumes the ones above it passed.

On Windows use `curl.exe`, not `curl` — in PowerShell `curl` is an alias for
`Invoke-WebRequest` and takes different flags.

---

## 0. What can be checked without Docker

These run against the host virtualenv and need nothing else.

```powershell
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m ruff format --check .
.venv\Scripts\python.exe run_tests.py
```

Expected: ruff clean, 289 passed, 3 skipped. The skips are the `db`-marked concurrency
tests — see step 6.

`run_tests.py` exists rather than a single `pytest` invocation because all three services
install a top-level package named `app`. In one pytest process whichever imports first
wins, and the others silently get the wrong models — so each suite runs in its own
subprocess, from its own directory. `--cov` adds a coverage column; naming suites
(`run_tests.py orders common`) runs a subset; anything after `--` is passed to pytest.

The `repo` suite is `tests/`, and it holds one check that matters more than it looks: it
runs each service's `alembic upgrade head` into a throwaway database and diffs the result
against that service's models. The per-service suites build their schema with
`create_all`, so without it a model could gain a column, every test stay green, and the
container fail to start on deploy. It also boots each service from `.env.example` to prove
no setting was renamed without the example being updated.

Per-service coverage (CLAUDE.md wants ≥ 70%):

```powershell
cd services\orders
..\..\.venv\Scripts\python.exe -m pytest --cov --cov-report=term-missing -q
```

---

## 1. Bring the stack up

```powershell
Copy-Item .env.example .env
docker compose up -d --build
docker compose ps
```

Expected: `postgres`, `redis`, `inventory`, `payments`, `orders` healthy; `worker`,
`beat`, `prometheus`, `grafana` running. First build takes a few minutes.

On a machine that is short of RAM, stop the two heavy containers until you need them:

```powershell
docker compose stop prometheus grafana
```

Check each service answers:

```powershell
curl.exe -s http://localhost:8001/health
curl.exe -s http://localhost:8002/ready
curl.exe -s http://localhost:8003/ready
```

Expected: `{"status":"ok"}` and `{"status":"ready","checks":{...}}`.

`/ready` is the interesting one — it is what proves the container can actually reach
PostgreSQL and Redis, not just that the process started.

---

## 2. Seed the catalogue

```powershell
.venv\Scripts\python.exe tools\seed.py --base-url http://localhost:8002 --count 50 --seed 7
curl.exe -s http://localhost:8002/products
curl.exe -s http://localhost:8002/products/SKU-0001
```

Expected: 50 products created, SKU-0001 upward, a few with `stock: 0`. `--seed 7` makes
the catalogue reproducible, so the same SKUs are out of stock every time.

`GET /products` returns the whole catalogue — it takes no `limit`, and FastAPI ignores
query parameters a route does not declare, so `?limit=3` would silently return all 50
rather than fail. Fine at 50 products; something to revisit if the catalogue grows.

---

## 3. One order, end to end

```powershell
curl.exe -s -X POST http://localhost:8001/orders `
  -H "Content-Type: application/json" `
  -H "Idempotency-Key: manual-test-1" `
  -d '{\"customer_email\":\"kumar@example.com\",\"items\":[{\"sku\":\"SKU-0001\",\"qty\":2}]}'
```

Expected: 201, `status` is `CONFIRMED` (or `FAILED` with a reason if that SKU is out of
stock or the simulated gateway declined — both are correct outcomes).

Now send **exactly the same request again**. Expected: the same order id, the same body,
and no new rows anywhere. That is M3's idempotency criterion.

Then fetch it back and look at the event trail:

```powershell
curl.exe -s http://localhost:8001/orders/<order-id>
```

Expected: `events` shows `PENDING -> RESERVED -> CONFIRMED`, each with a `request_id`.

---

## 4. M5 acceptance — traffic run

```powershell
.venv\Scripts\python.exe tools\traffic.py --rps 10 --duration 120 --mix 0.7
```

Expected: ≥ 95% success, counting an order that comes back `FAILED` because of the
configured 5% gateway decline rate as a success — the request worked, the card did not.
The summary separates those two things on purpose.

Afterwards, check nothing was left inconsistent:

```powershell
docker compose exec postgres psql -U app -d inventory_db -c "SELECT status, count(*) FROM stock_reservations GROUP BY status;"
docker compose exec postgres psql -U app -d orders_db -c "SELECT status, count(*) FROM orders GROUP BY status;"
```

Expected: no `ACTIVE` reservations belonging to orders that are `FAILED` or `EXPIRED`.
An `ACTIVE` row for an order still in flight is normal; one that outlives its order is
the bug.

---

## 5. M6 acceptance — follow one request id

Take the `X-Request-ID` header from any response, or pick one out of an error body:

```powershell
.venv\Scripts\python.exe tools\logtool.py trace <request-id>
```

Expected: lines from `orders`, `inventory` and `payments` in time order, with the gap in
milliseconds between them. That single view is the whole point of Phase 2.

Then try the others:

```powershell
.venv\Scripts\python.exe tools\logtool.py errors --since 15m
.venv\Scripts\python.exe tools\logtool.py slow --top 10 --since 15m
.venv\Scripts\python.exe tools\logtool.py order <order-id>
```

---

## 6. M1 acceptance — the concurrency tests

Each service has a `tests/test_concurrency.py` that is skipped by default. They need a real
PostgreSQL: SQLite takes a database-wide write lock, so the threads would queue up and the
tests would pass even against code that is wrong. Point them at the matching database:

**Point them at the `_test` databases, never at the live ones.** The fixtures in
`conftest.py` run `create_all`/`drop_all` and `DELETE FROM` every table between tests.
Aimed at `inventory_db` that would wipe the catalogue and drop the schema out from under
the running container. `infra/postgres/init.sql` creates the three `_test` databases
alongside the real ones; they live in the same instance, so the locking behaviour under
test is identical.

```powershell
docker compose up -d postgres

$env:TEST_DATABASE_URL = "postgresql+psycopg://app:app@localhost:5432/inventory_test"
cd services\inventory ; ..\..\.venv\Scripts\python.exe -m pytest -m db -q ; cd ..\..

$env:TEST_DATABASE_URL = "postgresql+psycopg://app:app@localhost:5432/payments_test"
cd services\payments ; ..\..\.venv\Scripts\python.exe -m pytest -m db -q ; cd ..\..

$env:TEST_DATABASE_URL = "postgresql+psycopg://app:app@localhost:5432/orders_test"
cd services\orders ; ..\..\.venv\Scripts\python.exe -m pytest -m db -q ; cd ..\..

Remove-Item Env:\TEST_DATABASE_URL
```

If the stack was built before those databases existed, create them once by hand:

```powershell
docker compose exec postgres psql -U app -d postgres -c "CREATE DATABASE inventory_test;"
docker compose exec postgres psql -U app -d postgres -c "CREATE DATABASE payments_test;"
docker compose exec postgres psql -U app -d postgres -c "CREATE DATABASE orders_test;"
```

Expected:

- **inventory** — 20 threads reserve 1 unit each against 10 in stock. Exactly 10 succeed,
  10 fail with `OUT_OF_STOCK`, final stock is 0. Never 11 successes, never negative stock.
  This is the M1 criterion, and the one that actually proves the conditional `UPDATE`.
- **payments** — concurrent charges for one order settle as a single payment. No
  double-charge.
- **orders** — concurrent requests carrying the same `Idempotency-Key` create one order.

Unset `TEST_DATABASE_URL` afterwards so the fast SQLite path comes back.

---

## 7. M7 acceptance — dashboard and alerts

Grafana: http://localhost:3000 (anonymous viewer is enabled; admin/admin to edit).
The **Order Platform** dashboard is provisioned automatically.

Start traffic in one terminal and watch the panels fill in. Then break something:

```powershell
docker compose stop payments
```

Expected, in order:

1. Within ~15s the payments target goes red at http://localhost:9090/targets.
2. `ServiceDown` moves to **Pending** at http://localhost:9090/alerts.
3. After 1 minute it moves to **Firing**.
4. Orders come back `201` with `status: FAILED` and
   `failure_reason: payments is unreachable` — the request succeeded, the payment could
   not be attempted. `UPSTREAM_ERROR` is the code on the `order_failed` log line, not the
   HTTP status. The reservation is released, so no stock is stranded.
5. `HighLatencyP95` also goes pending, because orders now blocks for the full
   `PAYMENTS_TIMEOUT_S` on every attempt. Worth knowing: one dead dependency lights up
   two alerts.

```powershell
docker compose start payments
```

Expected: the alert resolves within a scrape interval or two.

To see `OrdersStuckPending`, stop the worker and beat instead — orders will confirm but
nothing will expire or notify.

---

## 8. M8 acceptance — attach the debugger

```powershell
docker compose stop orders
$env:DEBUG_ATTACH = "1"
docker compose up -d orders
```

In VS Code, run the **Attach: orders** configuration. Put a breakpoint in
`services/orders/app/service.py` on the line that calls the payments client, then send an
order. Execution should stop there with the real request in scope.

**Attach: celery worker** does the same for background tasks — set `DEBUG_ATTACH=1` on
the `worker` container and break inside `send_confirmation`.

Set `DEBUG_WAIT=1` as well if the thing you are debugging happens during startup; the
process will then wait for you to attach before it runs anything.

---

## Known gaps

- Steps 0–7 were run on 2026-09-12 against the live stack and pass. Recorded results:
  1200 requests at 10 rps with 0 server errors and 0 transport errors (p50 21 ms,
  p95 409 ms); zero `ACTIVE` reservations left behind; `ServiceDown` pending at 60 s and
  firing at 61 s; 444 CONFIRMED orders and exactly 444 notification rows.
- Step 8 is verified only as far as it can be without a keyboard: `DEBUG_ATTACH=1` starts
  debugpy, port 5678 accepts a connection, the service still serves traffic, and the four
  `launch.json` attach configs point at the right ports. Actually hitting a breakpoint in
  VS Code has not been done.
- `services/orders/tests/test_concurrency.py` fails against PostgreSQL. It asserts both
  racing requests return an order id, which contradicts the `ORDER_IN_PROGRESS` behaviour
  the service deliberately has. See PROGRESS.md — the test needs rewriting, not the code.
- There is no end-to-end test that drives the live stack from pytest. The traffic run in
  step 4 plus the consistency queries do the same job by hand for now.
