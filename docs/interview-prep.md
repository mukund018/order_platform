# Interview prep — Q&A bank

**How to use this file.** These are drafted answers, not a script to recite. Read each
one, understand *why* it's true by checking the file/incident it points at, then answer
in your own words in an actual interview — a memorized paragraph is obvious and a
question that goes one level deeper than the memorized version will expose it. Where a
question is about an incident from INC-003 onward, the honest framing is: "I worked
through this with AI pairing on the diagnostic step, but I reviewed and understood the
root cause, the fix, and the test before it was closed, and I can explain the mechanism."
Say that if asked — it's true, and dodging the question reads worse than answering it.

---

## The project, in general

**1. What is this project and why did you build it?**
A realistic order-processing platform — three business services (orders, inventory,
payments) plus a worker and a support service — built specifically to practice
application support: monitoring, debugging, and diagnosing production incidents. It maps
directly to an Application Support Engineer job description rather than being a generic
CRUD app.

**2. Walk me through what happens when a customer places an order.**
`POST /orders` with an `Idempotency-Key` header. Orders prices the items against
inventory, inserts a `PENDING` order, calls inventory's `/reservations` (all-or-nothing
across every line), then calls payments. Success → commit the reservation, `CONFIRMED`,
queue a confirmation email. Any failure → release the reservation, `FAILED`. Every
transition writes an `order_events` row for audit. See `services/orders/app/service.py`
and the state machine in `app/state.py`.

**3. Why three separate databases instead of one?**
Each service owns its data; no cross-database foreign keys. It's slower to build than one
shared database, but it's what forces the real distributed-systems problems this project
exists to practice — compensation when a payment fails after stock is reserved,
reconciliation when two databases disagree about the same order (INC-004, INC-009), and
no way to enforce consistency except checking for it (see `docs/decisions.md`).

**4. Why integer paise instead of a float or Decimal for money?**
Floats lose precision on arithmetic that matters when it's money; paise as an integer
makes every amount exact and every calculation exact addition/subtraction, no rounding
error class of bug possible.

**5. What would you change if you rebuilt this from scratch?**
A real answer, not "nothing": no CI (a stopgap pre-push hook only protects the machine
it's configured on — INC-002 shipped specifically because of this gap); the frontend has
no automated tests; the reconciliation tools built during Phase 3 are on-demand, not
scheduled with an alert yet.

---

## Architecture and tech choices

**6. Why FastAPI over Flask/Django?**
Async-capable, Pydantic validation built in, automatic OpenAPI docs, and dependency
injection that's a natural fit for "give me a DB session, give me an HTTP client" per
request — comparable to Express middleware but typed and validated.

**7. Why synchronous SQLAlchemy/plain `def` endpoints instead of async?**
Every endpoint here does blocking work either way (a database call, an outbound HTTP
call), so async buys nothing without an async driver throughout, and FastAPI already runs
sync `def` endpoints in a threadpool. One code path for HTTP handlers and Celery tasks to
share was worth more than async's advantages the way traffic is actually shaped here.

**8. How does the product cache avoid serving stale data (cache-aside pattern)?**
Read-through: a miss reads the DB and repopulates the cache; a write (stock/price change)
invalidates the affected keys rather than updating them, so the next read is a clean
re-fetch, not a race to update the cache correctly. INC-002 is the cautionary tale for
getting this wrong — the invalidation call was removed and nobody noticed for two days
because nothing errored.

**9. How is stock decremented safely under concurrent orders?**
A conditional `UPDATE products SET stock = stock - :qty WHERE stock >= :qty`, not
read-then-write — the database, not the application, decides atomically whether there
was enough. Proven under real concurrency with a Postgres-only test: 20 threads racing 10
units of stock, asserting exactly 10 succeed and stock never goes negative.

**10. How do you prevent two orders from double-locking each other's rows (deadlock)?**
Reservation lines are always processed in a fixed SKU order — if two orders share two
products, both transactions acquire the rows in the same sequence, so a lock cycle is
structurally impossible rather than just unlikely. INC-011 is what happens when that
ordering is removed: a real, reproducible deadlock under load. Verified with a test that
fails against the broken code with a genuine Postgres deadlock, and passes with the fix.

---

## Observability

**11. What's in a structured log line and why JSON?**
Every line carries `timestamp`, `level`, `service`, `event`, `request_id`, and relevant
context (`order_id`, `duration_ms`, `status_code`). JSON because it's machine-parseable —
`tools/logtool.py` and the ops console's trace view both depend on being able to filter
and join log lines programmatically, which plain text logs don't support.

**12. How does a request ID travel across four services and a background worker?**
Middleware reads or mints `X-Request-ID` on the way in, binds it to a context variable so
every log line in that request picks it up automatically, and an httpx event hook forwards
it on every outbound call. Celery does the same via a `before_task_publish` signal
carrying it in the task headers. One ID, one story, across process boundaries.

**13. Give an example of a metric that caught something logs alone couldn't.**
`upstream_calls_total{outcome="timeout"}` — added after INC-001, where every existing
panel looked healthy (inventory's own latency, error rate) while orders was silently
abandoning calls inventory went on to answer successfully. The metric captures the
*caller's* view of the outcome, which nothing else did.

**14. What's the RED method and where does it show up here?**
Rate, Errors, Duration — the three questions the ops console's Overview screen answers in
that order, and the same three the Grafana dashboard's request-health row covers.

**15. Why does `OrdersStuckPending` matter more than it looks like it should?**
It can fire while every HTTP status code is 200 and every latency panel is normal —
orders quietly stop completing (INC-002's category: no errors, and the business is
losing money). It's the example of a business metric catching what a purely technical one
cannot.

---

## Incidents (the actual "software detective" work)

**16. Walk me through INC-001 end to end.** *(Your own work — know this one cold.)*
See the demo script (`docs/demo.md`) — it's built around exactly this incident.

**17. Walk me through INC-002 end to end.** *(Also your own work.)*
A stale-cache bug: the write path stopped invalidating the cache while the TTL was
raised thirty-fold, so restocked products kept reading as sold out — 12 SKUs, 720 units,
₹17,85,600 invisible to customers, zero errors logged, caught by a category manager two
days later, not by monitoring. The RCA is also honest about two mistakes made while
solving it: a test order that destroyed the evidence it was measuring, and a first draft
that blamed a missing regression test that turned out to already exist.

**18. What's the difference between a database performance problem (INC-003) and a
missing-schema-object problem (INC-010) — aren't they the same thing?**
Same underlying gap (a live database silently drifted from what the model and migration
say it should be), different consequence: INC-003's missing index made a query slow;
INC-010's missing constraint broke an idempotency guarantee entirely. `alembic check`
(wrapped in `tools/check_schema_drift.py`) catches both, because it compares the live
schema to the models directly rather than checking for one specific symptom.

**19. What's a cache stampede and how did INC-012 demonstrate one?**
When many cache keys expire at (nearly) the same instant, every in-flight request misses
simultaneously and hits the database at once. INC-012: a 5-second TTL (down from 60) plus
a connection pool capped at 2 — neither change unsafe alone, together a stampede against
a bottleneck that repeated every 5 seconds ("a heartbeat" in the latency panel). Fixed
with TTL jitter (±20%) so keys populated together don't expire together.

**20. What's the difference between mitigation and a permanent fix, with an example?**
Mitigation restores service fast and can be undone; the fix addresses why it happened.
INC-009: mitigation was releasing 15 stranded stock reservations through the real API;
the permanent artifact is `tools/reconcile_stock.py`, which finds this class of problem
again if it recurs, not just this one instance of it.

**21. Why does the payment-reconciliation task never auto-correct an order's status?**
By the time it runs, the stock a FAILED order held has likely already been released and
re-reserved by a different order — flipping the status back to CONFIRMED risks an
oversell on top of the original problem. The right action (refund, manual fulfilment)
depends on facts the system can't see, so it reports loudly (a log line with the amount
and provider ref, a Prometheus counter, an alert) and leaves the decision to a person.

**22. What did INC-006 (the last-unit bug) teach you about testing boundaries?**
An off-by-one (`stock > qty` instead of `>= qty`) rejected exactly-enough-stock requests
while accepting anything less — invisible unless you specifically test the boundary
(`qty == stock`), not just "enough stock" and "not enough." Any quantity check needs a
test at the exact boundary, not just comfortably inside and outside it.

**23. How do you tell a genuine concurrency bug from ordinary load-driven slowness?**
Fast failures vs. slow ones. INC-011's deadlocks were fast 500s (Postgres kills a
transaction immediately on detecting a cycle) — ordinary contention would show as
*slower*, not as errors. "Fails fast under load, and retrying usually works" is close to
a deadlock fingerprint.

**24. A symptom repeats on a fixed period. What does that tell you before you've read any
code?**
There's a timer behind it. INC-012's "heartbeat" period matched `PRODUCT_CACHE_TTL`
exactly — measuring the gap between spikes named the cause before any code was read.

**25. What's the actual lesson of INC-005 (everything is flaky at once)?**
Failures correlated across unrelated services point at something they share — a
database, a broker, a host — not at a bug in any one of them. Chasing one service's own
logs when three unrelated services alert within minutes of each other is the wrong first
move.

---

## Testing and process

**26. Why do three tests need real PostgreSQL and skip under SQLite?**
They test genuine row-level locking under concurrent transactions (the oversell test, the
INC-006 boundary, the INC-011 deadlock). SQLite takes a database-wide write lock, so
threads queue up and the test would pass even against code that's actually wrong —
proving nothing about the thing it claims to test.

**27. How did you verify a regression test actually catches the bug, rather than just
passing?**
For INC-011, by temporarily reverting the fix and re-running the test — it failed with a
genuine Postgres deadlock error, not a mocked assertion — then restoring the fix and
confirming the full suite passed. Same pattern for INC-010's schema-drift tool: dropped
the constraint again on purpose, confirmed the tool reported drift and named the exact
missing object, then restored it.

**28. Tell me about a mistake you (or the AI, working on this with you) made and how it
was caught.**
Mid-session, running the Postgres-only concurrency tests pointed `TEST_DATABASE_URL` at
the *live* databases instead of the dedicated `*_test` ones — the test fixtures' teardown
dropped every table in all three live databases. Recovered by clearing the stale
`alembic_version` state and letting the real migration path recreate the schema, then
built `common.testing.require_test_database`, which now refuses to run schema-dropping
fixtures against anything but SQLite or a `*_test`-suffixed database — proven against the
exact scenario that caused it. Full account in `docs/decisions.md`.

**29. What would you add if you had one more week?**
Wire the reconciliation tools (`reconcile_stock.py`, `check_schema_drift.py`) into a
schedule with alerts instead of leaving them on-demand; a schema-drift check would have
caught INC-010 before a ticket did. Second choice: automated frontend tests — the biggest
tested-by-hand gap in the project.

**30. Why should we hire an Application Support Engineer who built their own incidents
instead of just fixing real ones on the job?**
Because the diagnostic skill — form a hypothesis, pick the right tool, rule it out or
confirm it, write down what you found — is the transferable part, and this project forces
practicing it against problems with a known, gradeable answer before doing it against
problems that cost the business money on the first attempt.
