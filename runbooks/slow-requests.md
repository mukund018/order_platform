# Runbook — p95 latency above 1s

**Alert:** `HighLatencyP95` · **Severity:** SEV3, or SEV2 if orders are starting to time out

One service is taking over a second at the 95th percentile. Nothing is failing yet, but
`INVENTORY_TIMEOUT_S` is 2.0 and `PAYMENTS_TIMEOUT_S` is 3.0 — once p95 approaches those,
orders will start failing and this becomes a SEV2.

## First five minutes

Find out **which endpoint**, not just which service. The `path` label is the route
template, so this groups properly:

```
topk(5, histogram_quantile(0.95, sum by (path, le) (rate(http_request_duration_seconds_bucket{service="inventory"}[5m]))))
```

Then get concrete examples:

```powershell
.venv\Scripts\python.exe tools\logtool.py slow --top 10 --since 15m
```

Take the slowest request id and trace it. The gap between consecutive lines tells you
which hop is eating the time:

```powershell
.venv\Scripts\python.exe tools\logtool.py trace <request-id>
```

This is the single most useful step. If most of the second is between "called payments"
and "payments answered", the problem is not in the service that is alerting.

## Where the time usually goes

**A slow database query.** Check what PostgreSQL has been doing:

```powershell
docker compose exec postgres psql -U app -d inventory_db -c "SELECT calls, round(mean_exec_time::numeric,2) AS avg_ms, round(total_exec_time::numeric,2) AS total_ms, left(query, 90) AS query FROM pg_stat_statements ORDER BY total_exec_time DESC LIMIT 10;"
```

Sort by `total_exec_time`, not by `mean_exec_time` — a 20ms query run 10,000 times hurts
more than a 2s query run twice. Then take the offender and:

```powershell
docker compose exec postgres psql -U app -d inventory_db -c "EXPLAIN ANALYZE <the query>;"
```

A `Seq Scan` on a table that should be indexed is the usual answer.

**Something blocking.** Queries that are running right now:

```powershell
docker compose exec postgres psql -U app -d orders_db -c "SELECT pid, state, wait_event_type, wait_event, now()-query_start AS runtime, left(query,80) FROM pg_stat_activity WHERE state <> 'idle' ORDER BY runtime DESC;"
```

`wait_event_type = 'Lock'` means one transaction is waiting on another. Long `idle in
transaction` sessions are the usual culprit — something opened a transaction and went
away.

**The cache stopped helping.** If inventory got slow and Redis is unhealthy, every
product read is now hitting PostgreSQL. The service keeps working — the cache degrades
rather than failing — so the only signals are the latency change and a warning in the log.

```powershell
docker compose exec redis redis-cli info stats | Select-String keyspace
docker compose exec redis redis-cli info memory | Select-String used_memory_human
```

Never run `KEYS *` on a busy Redis; it blocks the server. Use `SCAN` if you must.

**The gateway.** Payments latency is partly configured on purpose:
`GATEWAY_LATENCY_MS_MIN` / `GATEWAY_LATENCY_MS_MAX`. Check `.env` before investigating a
latency that somebody set deliberately.

**Saturation.** If nothing individually is slow but everything is slower, look at the
*DB connection pool* panel and at `docker stats`. Requests waiting for a pool slot show up
as latency with no slow query behind them.

## Mitigation

Slow is better than down. Shed load before you start optimising: stop the traffic
generator, or accept the degradation while you find the query. If one endpoint is
responsible and it is not on the order path, that buys you time.

## Prevention

Every time this runbook ends in "a missing index", add the index **and** a regression test
that asserts the query plan or the row count, so the next person does not rediscover it.
