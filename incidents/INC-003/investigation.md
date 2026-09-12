# Investigation — INC-003

Severity: SEV2 — orders are still completing (not a full outage), but p95 is climbing
linearly with no deploy and no CPU pressure, and orders-service calls inventory/payments
with 2s/3.5s+ timeouts (see INC-001). If this keeps climbing it crosses those timeouts and
starts turning into real order failures — money at risk, not yet realized.
Impact: no orders lost yet; latency alert fired at 13:02, degrading since ~09:00. Trending
toward checkout failures if left alone.
Acknowledged at: 2026-09-12T13:18:21Z

| Time (UTC) | Hypothesis | Tool / action | Finding | Result |
|---|---|---|---|---|
| 13:19 | Load-driven degradation — will p95 climb visibly across a 10-15 min run? | `traffic.py --rps 15 --duration 900 --mix 0.35` + Prometheus `histogram_quantile(0.95, ...)` per minute | p95 spiked early (~3.5s) then **fell** to 0.24-0.45s and stayed there for the rest of the run. Not a load-driven, monotonically-climbing pattern within this window. | ruled out as the *load-volume* mechanism — but flagged the early spike as possibly cold-start noise, not yet fully explained |
| 13:24 | Connection pool / config drift in orders-service | `docker exec order-platform-orders-1 env \| grep -i POOL` | `DB_POOL_SIZE=5`, `DB_MAX_OVERFLOW=5` — matches documented defaults exactly, no drift | ruled out |
| 13:24 | Held-open transaction across outbound HTTP calls (a regression of a previously-fixed bug, see `docs/decisions.md`) | Read `services/orders/app/service.py` `_reserve`/`_settle`/`create_order` | `session.commit()` runs before every outbound call in every path; the fix is intact in the current code | ruled out |
| 13:25 | Postgres-side lock contention or a long-running transaction (would explain "restart didn't help" — a leak elsewhere on the server, not in the orders process) | `pg_stat_activity` filtered by `xact_start`, across the whole cluster | No open transaction anywhere older than the query itself | ruled out |
| 13:25 | Connection/pool exhaustion at the Postgres server level (shared across all 3 databases) | `SHOW max_connections` (100) vs `pg_stat_activity` grouped by database/state | ~24 total connections, all idle, nowhere near 100 | ruled out |
| 13:26 | Table bloat / autovacuum falling behind on `orders` (frequently UPDATEd 2-3x per row) | `pg_stat_user_tables` on `orders_db` — `n_dead_tup`, `last_autovacuum` | Dead tuples low (108 vs 2373 live), autovacuum running recently and often (4 runs) | ruled out |
| 13:27 | Missing/misconfigured per-table storage params (autovacuum disabled on a specific table) | `pg_class.reloptions` for all `orders_db` tables | All empty — no overrides | ruled out |
| 13:28 | No single SQL statement in `orders_db` or `payments_db` is individually slow (checked both, sorted by mean and by calls) — bottleneck must be elsewhere, or in a different database entirely | `pg_stat_statements` (reset first, to exclude noise from earlier unrelated recovery work), filtered per-database via `pg_stat_statements JOIN pg_database` | Every statement in `orders_db` and `payments_db` sub-millisecond mean. Stuck — took hint 1. | — |
| 13:28 (hint 1) | *"Latency grows with time, not rate; restart didn't reset it. What gets bigger every hour and never smaller?"* | re-read the schema for a table that is insert-only / never shrinks | `stock_reservations` — rows are only ever status-transitioned (ACTIVE→COMMITTED/RELEASED), never deleted. Strong candidate for an O(n)-with-table-size scan. Took hint 2 to confirm which service to look inside. | — |
| 13:31 (hint 2) | *"Split p95 per service, look inside the slow one"* | Prometheus per-service `histogram_quantile(0.95, ...)` for orders/inventory/payments | orders and payments both showed some elevation; inventory's own p95 was actually the *lowest* of the three even though inventory owns `stock_reservations`. The service-level split didn't point cleanly at inventory — took hint 3. | — |
| 13:31 (hint 3) | *"Find the highest total_exec_time statement in inventory_db, EXPLAIN it"* | `pg_stat_statements` on `inventory_db` sorted by `total_exec_time` (not mean — a cheap-per-call query run thousands of times still dominates total DB time) | Top query: the reservation lookup by `order_id` joined to `products`, 5435+839 calls, ~1.9s combined total time | confirmed as the right query to inspect |
| 13:31 | Run `EXPLAIN ANALYZE` on that exact query | `EXPLAIN ANALYZE SELECT ... FROM stock_reservations ... WHERE order_id = ... AND status IN (...)` | **`Seq Scan on stock_reservations`, `Rows Removed by Filter: 2137`** — a full table scan for a single-order lookup | **confirmed**: this is the mechanism |
| 13:31 | Is the index actually missing, or is Postgres just choosing not to use it? | `\d stock_reservations` | Only `pk_stock_reservations` (on `id`) exists. **No index on `order_id` at all** | root cause located |
| 13:31 | Is this a code bug (model/migration never defined the index) or something that happened at runtime? | Read `services/inventory/app/models.py` and `alembic/versions/0001_initial.py` (current code, not the incident branch's diff) | **Both correctly define and create `ix_stock_reservations_order_id`.** The code has never been wrong. The index exists in every migration and every model definition, and is simply *not present in the live database* | confirmed: this is a runtime/data-plane fault, not an application bug — matches the "some faults touch no code at all" design of Phase 3 |
| 13:31 | Mitigate: recreate the index directly | `CREATE INDEX IF NOT EXISTS ix_stock_reservations_order_id ON stock_reservations(order_id);` then re-run the same `EXPLAIN ANALYZE` | Plan changed to `Bitmap Index Scan on ix_stock_reservations_order_id`; execution time 0.628ms → 0.123ms even at this small (~2100 row) test scale | **mitigated** at 13:31:44Z |

## Notes

**Why the load test didn't show a dramatic climb.** At the table's current size (~2,100
rows after ten minutes of synthetic load), even a full sequential scan costs well under a
millisecond — the plan proves the *mechanism* (`Seq Scan`, cost scales with row count),
but this test never accumulated the row count a real production day would, so the
user-visible symptom in the ticket (p95 climbing past a second) reflects hours of real
traffic, not what a 10-minute local repro can fully replay. The evidence for the root
cause is the query plan itself, not a reproduced multi-second p95.

**The early p95 spike (~3.5s) noted in the first hypothesis** happened in the same
window the index was actually missing, so it may have been a genuine (if noisy, low-
volume) early sample of the real effect, not purely cold-start artifact as first assumed —
left as an open question since it settled before it could be isolated cleanly.

**Hints used: 3** (of 4 available for this difficulty tier). Recorded honestly per this
session's rule: these incidents are being closed by the AI at Kumar's explicit request,
not diagnosed blind by him, so the hint count and timings reflect the AI's investigation,
not a claim about Kumar's own performance.
