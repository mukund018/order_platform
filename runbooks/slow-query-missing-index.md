# Runbook — latency climbs with cumulative data, not with current load

**Symptom:** a service gets slower over hours, CPU on every container looks normal,
restarting the slow-looking service does not help, and no single request stands out as
the cause — it is *everything through that service* that is a bit slower than an hour ago.

This is the signature of a query whose cost is proportional to table size, most often a
missing or dropped index. It is different from
[db-pool-exhausted.md](db-pool-exhausted.md) (which spikes and recovers with load, not
with time) and from [stale-catalogue-data.md](stale-catalogue-data.md) (wrong data, not
slow data).

## Why a restart does not help

There is nothing to restart away. The process holds no leaked state — the cost lives in
the database, in the shape of a query plan, and a plan is decided fresh on every query
against whatever the table looks like right now. If the table keeps growing and the plan
is a sequential scan, every restart hands you the exact same problem a few seconds later.

## Confirm it

**Find the query, by total cost, not average cost.** A cheap-per-call query run
thousands of times can dominate total database time even when its *mean* looks fine — sort
by `total_exec_time`, not `mean_exec_time`, or you can miss it entirely (this is exactly
what happened in INC-003: every individual number looked healthy sorted by mean).

```powershell
docker compose exec postgres psql -U app -d <db> -c "
SELECT s.calls, round(s.mean_exec_time::numeric,2) AS mean_ms, round(s.total_exec_time::numeric,1) AS total_ms, left(s.query,150) AS query
FROM pg_stat_statements s JOIN pg_database d ON s.dbid = d.oid
WHERE d.datname = '<db>' ORDER BY s.total_exec_time DESC LIMIT 10;"
```

**Read the plan.** Run the actual query (with a real value substituted in) through
`EXPLAIN ANALYZE`. `Seq Scan` on anything but a tiny table, especially with a large `Rows
Removed by Filter`, is the tell.

```powershell
docker compose exec postgres psql -U app -d <db> -c "EXPLAIN ANALYZE <query>;"
```

**Compare the live schema against what the code expects.** `\d <table>` against the
model's `__table_args__` / the Alembic migration that creates it. If the code says an
index should exist and the database disagrees, something changed the database directly,
outside any migration — a manual `DROP INDEX`, a bad hotfix, a restore from an older
backup. `git diff` on the code will show you nothing, because the code was never wrong.

## Mitigation

Recreate the index directly against the live database:

```powershell
docker compose exec postgres psql -U app -d <db> -c "CREATE INDEX IF NOT EXISTS <name> ON <table>(<column>);"
```

Verify with the same `EXPLAIN ANALYZE` — the plan line should change from `Seq Scan` to
`Index Scan` or `Bitmap Index Scan`.

## Prevention

- A regression test that asserts the query plan directly (`EXPLAIN` + assert no `Seq
  Scan`), not just that the query returns correct rows — correctness was never the
  problem, cost was. See `services/inventory/tests/test_reservation_query_plan.py`.
- The real gap this exposes: nothing currently checks that the live schema matches
  `alembic heads` on a schedule or at startup. Migrations are only a source of truth if
  something verifies the database still agrees with them.
