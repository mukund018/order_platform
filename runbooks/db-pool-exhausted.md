# Runbook — database connection pool exhausted

**Symptom:** requests slow down or time out, no single query is slow, and the
*DB connection pool* panel shows `in_use` pinned at its ceiling.

Each service runs a pool of `DB_POOL_SIZE` connections plus `DB_MAX_OVERFLOW` extra — five
and five by default, so ten in total. When all ten are checked out, the eleventh request
waits. It does not fail; it queues. That is why this shows up as latency with no slow
query behind it, which makes it one of the more confusing failures to diagnose.

## Confirm it

The panel is the fastest check. In PromQL:

```
db_pool_connections{state="in_use"} / (db_pool_size + 5)
```

Anything sitting near 1 for more than a few seconds is saturation, not a spike.

From the database side:

```powershell
docker compose exec postgres psql -U app -d orders_db -c "SELECT state, count(*) FROM pg_stat_activity WHERE datname = 'orders_db' GROUP BY state;"
```

## What the state tells you

**Lots of `active`** — connections are genuinely busy running queries. The pool is a
symptom; the real problem is query time or request volume. Go to
[slow-requests.md](slow-requests.md).

**Lots of `idle in transaction`** — this is the bad one. A transaction was opened and
never committed or rolled back, so the connection is held and, worse, it is holding locks.

```powershell
docker compose exec postgres psql -U app -d orders_db -c "SELECT pid, now()-state_change AS idle_for, left(query,100) FROM pg_stat_activity WHERE state = 'idle in transaction' ORDER BY 2 DESC;"
```

The `query` column shows the last statement that ran on that connection, which usually
points straight at the code path that leaked it. Look for a session opened without a
`with` block, or a transaction held open across a network call — the second one is the
classic: the transaction stays open for the whole duration of an HTTP request to another
service, so one slow downstream turns into ten held connections.

**Lots of `idle`** — connections are pooled and free at the database, but the application
thinks they are checked out. That means the application leaked them: a `Session` that was
never closed.

## Mitigation

Restarting the service drops every connection and clears the pool immediately. Do it if
orders are failing — but take `pg_stat_activity` output first, because the restart
destroys the evidence you need to stop it happening again.

To kill one specific stuck session without restarting anything:

```powershell
docker compose exec postgres psql -U app -d orders_db -c "SELECT pg_terminate_backend(<pid>);"
```

Raising `DB_POOL_SIZE` is tempting and is almost always wrong as a first move. If
connections are being leaked, a bigger pool just takes longer to exhaust. If they are
genuinely busy, a bigger pool moves the queue from the application to PostgreSQL, which
has its own `max_connections` limit.

## Prevention

The pool sizes here are deliberately small (5 + 5) so that this failure is reachable
without generating serious load. That is a training decision, not a production one — a
real deployment would size the pool against `max_connections` divided by the number of
service instances.
