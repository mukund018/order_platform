---
id: INC-003
title: missing index on stock_reservations.order_id, dropped from the live database out-of-band
severity: SEV2
services: [inventory, orders]
category: Database performance
detected_at: 2026-09-12T13:17:00Z
mitigated_at: 2026-09-12T13:31:44Z
resolved_at: 2026-09-12T13:34:23Z
time_to_acknowledge_min: 1
time_to_mitigate_min: 15
hints_used: 3
---

## Summary

Every call to reserve, commit, or release stock for an order looks up
`stock_reservations` by `order_id`. That column's index — `ix_stock_reservations_order_id`
— was correct in the SQLAlchemy model and in the checked-in Alembic migration, but was
**missing from the running database**: something dropped it directly, outside any
migration. Every one of those lookups did a full sequential scan of the whole table
instead. `stock_reservations` is insert-only — rows are only ever status-transitioned
(ACTIVE → COMMITTED/RELEASED), never deleted — so the table only grows, and the scan cost
grows with it. That is the entire mechanism behind "checkout gets slower through the day
and a restart doesn't fix it": the app has no persistent state to restart away, and the
missing index isn't something any deploy touched.

## Impact

No orders were lost during this incident (SEV2, not SEV1 — degradation, not an outage).
At the ~2,100-row scale reached during this investigation's own load test, the missing
index cost was only fractions of a millisecond per lookup (confirmed directly:
`EXPLAIN ANALYZE` before the fix: 0.628ms via `Seq Scan`, `Rows Removed by Filter: 2137`;
after: 0.123ms via `Bitmap Index Scan`). The reported symptom — p95 past a second and
climbing — reflects a full production day's accumulated row count, which this local
investigation did not reproduce at 1:1 scale. The mechanism is proven by the query plan,
not by reproducing the full-severity number.

The real risk this incident represents: at production volume, this compounds until
`orders-service`'s own outbound timeouts to inventory (`INVENTORY_TIMEOUT_S`, see INC-001)
start tripping, which turns a slow reservation lookup into real order failures.

## Timeline (UTC)

| Time | Event |
|---|---|
| ~13:17 | Fault injected (index dropped from the live database); ticket received |
| 13:18 | Acknowledged, triaged SEV2 |
| 13:19–13:28 | Ruled out: load-volume correlation, connection pool config drift, the
previously-fixed "transaction held open across an outbound call" bug, Postgres-side lock
contention, connection exhaustion, and table bloat/autovacuum lag on `orders_db` |
| 13:28 | No individual query in `orders_db`/`payments_db` was slow by any measure — hint 1 taken |
| 13:31 | Hint 2 taken (per-service p95 split didn't point cleanly at inventory on its own) |
| 13:31 | Hint 3 taken — pointed at `inventory_db`'s highest-*total*-exec-time statement |
| 13:31 | `EXPLAIN ANALYZE` on that statement showed `Seq Scan`, confirming the mechanism;
`\d stock_reservations` confirmed the index was simply absent |
| 13:31:44 | **Mitigated** — `CREATE INDEX` run directly against the live database; verified
via the same `EXPLAIN ANALYZE`, now `Bitmap Index Scan` |
| 13:34 | Regression test added and passing against real PostgreSQL; **resolved** |

## Root cause — 5 Whys

1. **Why did checkout get slower?** Every reservation lookup by `order_id` scanned the
   entire `stock_reservations` table instead of using an index.
2. **Why was it scanning the whole table?** The index on `stock_reservations.order_id`
   did not exist in the running database.
3. **Why didn't it exist?** It is not missing from the code — the SQLAlchemy model and the
   Alembic migration (`0001_initial.py`) both correctly define and create it. It was
   removed from the live database directly (a `DROP INDEX`), independent of any code path.
4. **Why did nobody notice before the alert fired?** Nothing in the platform checks that
   the indexes the code assumes exist actually still exist at runtime — `/ready` checks
   that the database is reachable, not that its schema matches expectations.
5. **Why does this class of fault matter?** Because it is invisible to every tool that
   only looks at code: `git diff`, code review, and even the test suite (SQLite doesn't
   need this index to pass functionally-correct tests; only a real Postgres query plan
   shows the difference). The only way to catch it is to look at the running database
   directly — which is exactly the "software detective" skill this project exists to
   practice.

## Resolution

**Mitigation** (13:31:44Z): `CREATE INDEX IF NOT EXISTS ix_stock_reservations_order_id ON
stock_reservations(order_id);` run directly against the live database. Verified with
`EXPLAIN ANALYZE` — plan changed from `Seq Scan` to `Bitmap Index Scan`.

**Permanent fix:** there is no code to fix — the model and migration were never wrong.
The regression test (`services/inventory/tests/test_reservation_query_plan.py`) asserts
the query plan directly: it runs `EXPLAIN` on the exact reservation-lookup query and fails
if `Seq Scan on stock_reservations` appears anywhere in the plan. This is deliberately a
plan-shape assertion, not a "does the query return the right rows" test — the query
already returned correct results even with the index missing; correctness was never the
problem, only cost.

## Prevention

- **Regression test added** (`test_reservation_query_plan.py`, Postgres-only, gated by
  `TEST_DATABASE_URL` like the existing concurrency tests) — catches this exact fault if
  it ever recurs, from any cause, not just this specific injected one.
- **Runbook added** (`runbooks/database-performance-missing-index.md`) — the
  `pg_stat_statements` (sort by `total_exec_time`, not `mean`) → `EXPLAIN ANALYZE` →
  `\d <table>` sequence that found this, generalized for the next person.
- **Gap not yet closed:** nothing currently verifies that expected indexes exist at
  startup or on a schedule. A `/ready` check or a periodic job comparing
  `pg_indexes` against the models would have caught this before an alert did. Not built in
  this session — left as an open item, the same way INC-002's "no consistency check
  comparing the API against the database" is still open.

## Lessons learned

- **`pg_stat_statements` sorted by `mean_exec_time` hid this fault; sorted by
  `total_exec_time` it was the top result.** A cheap query called thousands of times can
  cost more in aggregate than an expensive one called rarely, and only the second sort
  order surfaces that. This is now the lead investigative step in the runbook.
- **A missing index is invisible to a diff, a code review, and a SQLite-backed test suite
  all at once.** The only tool that would ever have shown it is the one built to look at
  the database's actual, current state — not its source of truth in version control.
- **The synthetic load test proved the mechanism, not the magnitude.** Ten minutes of
  traffic never grew the table to the size a real production day would, so the
  investigation had to conclude from the query plan (`Seq Scan`, cost scales with row
  count) rather than from directly reproducing a multi-second p95. Worth remembering for
  future "gets slower over hours" tickets: a short local repro can confirm the mechanism
  without ever reaching the full-severity number, and that is still a complete answer.
