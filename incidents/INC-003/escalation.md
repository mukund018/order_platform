To: DBA / platform team
Severity: SEV2 | Started: 2026-09-12T13:17:00Z | Ongoing: no — mitigated 13:31:44Z

Summary: `ix_stock_reservations_order_id` was missing from the live `inventory_db`. The
index is correctly defined in both the SQLAlchemy model and the checked-in Alembic
migration (`0001_initial.py`) — it was removed directly from the running database,
outside any migration or deploy we can find a record of.

Impact: no orders lost (SEV2, not SEV1). Every reserve/commit/release call did a full
table scan of `stock_reservations` instead of an index lookup; cost grows with the
table's row count, which only ever increases (rows are status-transitioned, never
deleted). At current scale (~2,100 rows) the added cost was sub-millisecond; left
unaddressed at production volume it would eventually trip `orders-service`'s own
outbound timeouts and turn into real checkout failures.

Evidence:
- `EXPLAIN ANALYZE` before mitigation: `Seq Scan on stock_reservations`, `Rows Removed by
  Filter: 2137`, 0.628ms.
- `EXPLAIN ANALYZE` after: `Bitmap Index Scan on ix_stock_reservations_order_id`, 0.123ms.
- `\d stock_reservations` showed only `pk_stock_reservations` present.
- `pg_stat_statements` (`inventory_db`, sorted by `total_exec_time`) showed the
  reservation-lookup query as the top cumulative cost, ~1.9s combined over ~6,270 calls
  in a 10-minute window, despite a low per-call mean.

Ruled out: application code (model and migration both correct — see
`services/inventory/app/models.py` and `alembic/versions/0001_initial.py`), the
previously-fixed "transaction held open across an outbound call" bug (still fixed, still
correct), connection pool exhaustion (~24 of 100 connections in use, all idle), table
bloat (`n_dead_tup` low, autovacuum running on schedule), and any config drift in
`orders-service`'s pool settings.

Ask: we don't have a record of what dropped this index or when — no deploy, no migration,
no application code path does it. Please check whether anything with direct database
access (a manual maintenance script, an ad hoc session, a restore) ran a `DROP INDEX`
against `inventory_db` around this window, and whether direct write access to production
schemas should be tightened so this can't happen silently again. We've mitigated
(recreated the index) and added a regression test that asserts the query plan, but that
only catches this one query — it doesn't prevent someone with access from dropping a
different index on a different table next time.
