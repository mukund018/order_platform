# Decisions

Short notes on the choices that were not obvious, and what the alternative was.
Newest at the bottom. If a decision is reversed later, leave the old entry and add a
new one saying why.

---

## 1. Three databases in one PostgreSQL instance

Each service owns `orders_db`, `inventory_db` or `payments_db` and never queries
another service's tables. There are no cross-database foreign keys — `stock_reservations.order_id`
is just a uuid column with an index on it.

That is the point. A shared schema would make the whole thing one application wearing
three hats, and none of the interesting failures (a reservation that was never released,
a payment that succeeded while the order says FAILED) would be possible. Running them in
one PostgreSQL process is only a convenience for a laptop; the code does not depend on it.

Cost: no referential integrity across services, so consistency has to be maintained by
the application. `expire_stale_orders` and the compensation in the order flow exist
because of this.

---

## 2. Synchronous SQLAlchemy, plain `def` endpoints

FastAPI is async, so async SQLAlchemy plus psycopg's async driver was the obvious
alternative. Chose sync instead:

- The Celery worker is synchronous. With a sync session the worker and the HTTP handlers
  use exactly the same domain functions. With an async ORM the worker needs a second
  engine and either `asyncio.run` per task or a duplicated code path.
- FastAPI runs `def` endpoints in a threadpool, so a slow query blocks one thread, not
  the event loop.
- The interaction worth understanding — pool size, threadpool size, and what happens when
  a downstream call gets slow — is easier to see and to explain in sync code.

Cost: concurrency is bounded by the threadpool (40 by default) and by `DB_POOL_SIZE +
DB_MAX_OVERFLOW`. That is a real ceiling, and it is deliberately left low (5 + 5) so
resource exhaustion is reachable in Phase 3.

---

## 3. Stock is decremented with a conditional UPDATE

The requirement is that 20 concurrent reservations against 10 units never oversell.

Rejected — read, subtract, write:

```python
product = session.get(Product, pid)
product.stock -= qty        # two transactions both read 10, both write 9
```

Two transactions can read the same value and both write back a decrement, losing one.
This is the classic lost update, and `READ COMMITTED` (PostgreSQL's default) does not
prevent it.

Considered — `SELECT ... FOR UPDATE`: correct, and it is the right tool when you need to
read a value, make a decision in Python, and write it back. It costs a round trip and
holds the row lock for the whole transaction.

Chosen — a single conditional statement:

```sql
UPDATE products SET stock = stock - :qty
 WHERE id = :id AND stock >= :qty
```

The read and the write happen inside one statement, so PostgreSQL serialises them on the
row itself. `rowcount == 0` means the guard failed, which is exactly "not enough stock".
No explicit lock, one round trip, and the check cannot drift away from the update.

The `stock >= 0` check constraint stays anyway, as a second line of defence: if the
conditional update is ever rewritten badly, the database rejects the row rather than
silently going negative.

### Ordering, and why it matters

Reservation lines are processed **sorted by SKU**. Two orders that both want A and B, one
arriving in the order A,B and the other B,A, will each hold one row lock and wait for the
other. PostgreSQL detects the deadlock and kills one transaction — it is not a
correctness bug, but it is an error under load that looks random. A consistent global
ordering removes it entirely.

---

## 4. `common/` is a real installed package

Logging config, request ids, error shape, health and metrics live in one package
installed into all three services rather than being copy-pasted.

The alternative — a shared file copied per service — drifts within a week, and then an
incident investigation turns up three different error bodies. The cost is that `common`
is now an interface: changing it changes three services at once.

---

## 5. The catch-all 500 handler is middleware, not `@app.exception_handler(Exception)`

Registering a handler for `Exception` in FastAPI installs it on Starlette's
`ServerErrorMiddleware`, which sits **outside** every middleware you add yourself. By the
time it runs, our request-id middleware has already cleaned up its context, so the error
body came out as `"request_id": null` — on exactly the responses where the id matters
most.

`UnhandledErrorMiddleware` sits inside the request-id middleware and catches the exception
while the id is still bound. The `@app.exception_handler(Exception)` registration is kept
as a last resort for failures further out in the stack, so even then the response is JSON
and never an HTML error page.

---

## 6. Tests use SQLite by default, PostgreSQL on demand

`pytest` with no environment set builds the schema in a throwaway SQLite file. Set
`TEST_DATABASE_URL` to a PostgreSQL URL and the same tests run there instead.

This keeps the suite runnable on a laptop with no server, which matters because a test
suite you cannot run is a test suite you stop running. The models are written to be
dialect-portable (`sqlalchemy.Uuid`, `DateTime(timezone=True)`, `Enum(...)`) to make it
work.

The honest cost: SQLite does not implement `SELECT ... FOR UPDATE`, has no real
concurrency, and stores timestamps without a timezone. Anything that depends on those is
marked `@pytest.mark.db` and **skips** without PostgreSQL — including the oversell test,
which is the one that actually proves decision 3. Run it before trusting that code:

```
docker compose up -d postgres
setx TEST_DATABASE_URL "postgresql+psycopg://app:app@localhost:5432/inventory_db"
pytest -m db
```

---

## 7. The Celery worker runs a thread pool and exposes port 9100

Two problems solved by one choice.

Celery's default prefork pool forks N child processes. Each child would have its own
`prometheus_client` registry, and each would try to bind the metrics port — so the
scraped numbers would be whichever child won the race. Multiprocess mode
(`PROMETHEUS_MULTIPROC_DIR`) fixes that properly but adds a shared directory and a
cleanup story.

The tasks are all database and HTTP waits, with no CPU-bound work, so a thread pool is
the right shape anyway: `--pool=threads --concurrency=4`. One process, one registry, and
`prometheus_client.start_http_server(9100)` is enough for Prometheus to scrape a service
that has no HTTP API of its own.

---

## 8. A declined payment is HTTP 200

`POST /payments` returns 200 with `status: FAILED` when the card is declined, not 402.

The request was understood and processed correctly; the *business outcome* was a decline.
Returning a 4xx would make the payments error-rate panel and the `HighServerErrorRate`
alert light up every time a customer's card is declined, which trains everyone to ignore
them. Transport and server problems get error status codes; business outcomes get a
status field.

Same reasoning in orders: `POST /orders` returns 201 with `status: FAILED` when stock ran
out. The order was created and its outcome recorded — that is a success from the API's
point of view.

---

## 9. Open question — the payment timed out but the customer was charged

The flow releases the reservation and marks the order FAILED when the payment call times
out. But a timeout is not an answer: the gateway may have approved the charge a
millisecond after we gave up. The customer is then charged for an order that our database
says failed, and the stock has gone back on the shelf.

What the system does today: nothing. The `payments` row stays PENDING (the service writes
PENDING before calling the gateway and only updates it on a real answer), so the evidence
that something is unresolved *is* recorded — it is simply not acted on.

What it should do, roughly in order of effort:

1. A reconciliation job: periodically find `payments` rows stuck in PENDING past a
   threshold, ask the gateway for the real status by `order_id`, and settle them —
   either refund, or move the order forward to CONFIRMED.
2. An alert on the count of PENDING payments older than a few minutes, so nobody finds
   out from a customer.
3. Long term, the gateway call should carry an idempotency key of our own so a retry can
   be proven safe, and the "did it go through" question can be answered by asking rather
   than guessing.

This is deliberately left unbuilt. It is the most realistic distributed-systems problem
in the project and it is worth being able to talk about honestly rather than pretending a
timeout is a failure.

---

## 10. Cache failures degrade, they never propagate

Every Redis call in `inventory/app/cache.py` is wrapped. If Redis is down or slow, the
request falls through to PostgreSQL and a warning is logged.

A cache exists to make a system faster. If it can also make it *unavailable*, it has made
things worse. The cost is that a Redis outage shows up as a latency change and a log
line, not as an error — which is precisely the kind of quiet degradation Phase 3 should
include an incident for.

---

## 11. A timeout from inventory leaves the order PENDING, not FAILED

The obvious thing to do when `POST /reservations` times out is to mark the order FAILED and
release the stock. That is wrong.

A timeout is not an answer. The reservation may have committed a millisecond after we gave
up, or it may never have run. Releasing on a guess means calling release for stock that was
never taken — and because release is deliberately forgiving, it succeeds silently and the
next order oversells.

So the order is left `PENDING` and the caller gets `504 UPSTREAM_TIMEOUT`. Nothing is
guessed. `expire_stale_orders` comes along within `ORDER_EXPIRY_MINUTES`, calls release
(which is idempotent and safe whether or not stock was taken), and moves the order to
`EXPIRED`.

The cost is that an order takes up to 15 minutes to resolve instead of failing instantly.
That is the right trade: a slow correct answer beats a fast wrong one when the wrong one
sells stock twice.

The same reasoning is why `expire_stale_orders` will not move an order to `EXPIRED` if the
release call failed — `EXPIRED` is terminal, and marking it would strand the stock forever.
It leaves the order alone and tries again next tick, giving up only after six hours, at
which point the `failure_reason` says the stock was never released so a human can find it.

---

## 12. Replaying an idempotency key for an *unfinished* order is a 409

The first version returned the existing order for any replay, which sounds like what
idempotency means. It produced a bad failure mode: a client whose first request timed out
retries, gets `201` with `status: PENDING`, and reasonably concludes the order was placed.
It was — and it is dead, waiting to be expired.

Now a replay returns the order only if it has finished. An unfinished one is
`409 ORDER_IN_PROGRESS` with the order id, which is a true statement the client can act on.

Resuming the flow instead was considered and rejected. If the first attempt is still in
flight, both requests would reserve and charge; payments answers the second with
`PAYMENT_IN_PROGRESS`, the order flow turns that into a failure, and the order ends FAILED
with its stock released while the first request's charge succeeds. That is money taken for
an order the system says failed — strictly worse than the dead end it was meant to fix.

The key is also checked against the request body (`customer_email` plus the sorted
`(sku, qty)` lines). Idempotency keys are client-generated and global, so without that
check a collision hands one customer another customer's order with a `201`.

---

## 13. support-service owns nothing and writes nothing

The ops console needs traces, error groups and service health in a browser.
`tools/logtool.py` already answers those questions on the command line, so the options
were to reimplement them in TypeScript against raw log files, or to put an HTTP front end
on the code that already exists.

The second, with two constraints.

**The analysis code moved to `common/logsearch.py`.** The CLI and the API now call the same
functions. "What counts as an error line", "which request was slowest", "how a trace is
stitched together" — if the console and the terminal ever disagreed about any of those, the
console would be worse than useless during an incident, because you would not know which
one was lying.

**The service is read-only, all the way down.** No database, no migrations, no writes. The
log volume is mounted `:ro` and so is `incidents/`. A support tool that can change the
system it is diagnosing is a support tool you cannot trust at 3am, and the mount is what
makes that a guarantee rather than an intention.

It also logs to stdout rather than into the log directory it reads. That was not the
original plan — the read-only mount rejected the file handler on first boot — but it is the
right answer: a service that wrote its own warnings into the directory it searches would
report on itself, and its own errors would show up on its own error board.

---

## 14. One origin for the browser, so no service needs CORS

nginx serves the built SPA and reverse-proxies `/api/orders/`, `/api/inventory/`,
`/api/payments/`, `/api/support/` and `/api/prom/` to the containers. The browser only ever
talks to `localhost:5173`.

The alternative is `CORSMiddleware` on four services, each with its own list of allowed
origins to keep in step, and a preflight request in front of every call. The proxy is one
file, it is how this would be deployed anyway, and it keeps support-service reachable from
the console without publishing it to anyone else.

The dev server mirrors the same prefixes in `vite.config.ts`, so `npm run dev` and the
container build run identical client code.

---

## 15. Phase 3 faults are sealed, not hidden

The point of an incident exercise is that the person investigating does not already know
the answer. When the same person writes the faults, that is hard to keep true.

`incidents/faults/INC-0XX.json` splits each incident in two. The half you are allowed to
read — ticket, category, the load profile needed to reproduce it — is plain text. The half
that gives it away is base64 in a `spoiler` field: not encryption, just a speed bump that
stops `cat` or `grep` spilling it by accident and makes decoding it a deliberate act.
`chaos.py reveal` refuses to print it until `rca.md` has actually been written.

Four of the twelve touch no application code at all — environment values, a compose
override, or rows in the database — so `git diff` on the incident branch would not help
even if you cheated. The code faults are committed under one neutral message,
`chore: INC-0XX environment setup`.

---

## Later — noted, not built

Out of scope per CLAUDE.md section 10, written down so the reasoning is not lost:

- **Authentication.** Every endpoint is unauthenticated. Real intake would need at least
  a service token between orders and its two dependencies.
- **Log rotation.** `logs/<service>.log` grows without bound; there is a plain
  `FileHandler` behind it. Fine for a dev stack, and it makes "the disk filled up" a
  reachable failure. `RotatingFileHandler` is a one-line change when it stops being funny.
- **Outbox pattern.** `send_confirmation` is enqueued after the transaction commits. If
  the process dies in between, the order is CONFIRMED and no notification is ever sent.
  An outbox table written in the same transaction, drained by a worker, is the standard
  fix.
- **Retries on the inventory and payments calls.** There are none, deliberately: a retry
  on a non-idempotent POST is how you double-charge people. Adding them means adding
  idempotency keys to those calls first.
- **CI.** No pipeline. `ruff check . && pytest` is the whole gate, run by hand.
