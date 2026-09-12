# Support toolkit

Which tool to reach for first, by symptom. The point of this page is to stop the
investigation starting with "let me read the code" — that is usually the slowest route to
the answer, and on a system you did not write it is the worst one.

## Symptom to first tool

| Symptom | First tool | Then |
|---|---|---|
| A customer gives you an order id | `logtool order <id>` | Read `order_events` in `orders_db` for the state trail |
| A customer or a log gives you a request id | `logtool trace <id>` | Follow the biggest gap to the slow service |
| "Orders are failing" | Grafana → *Error rate by service* | `logtool errors --since 15m` to get the codes |
| "Everything is slow" | Grafana → *p95 latency by service* | `logtool slow --top 10`, then `pg_stat_statements` |
| One endpoint is slow, others are fine | `pg_stat_statements` | `EXPLAIN ANALYZE` on the offending query |
| Slow with no slow query behind it | Grafana → *DB connection pool* | `pg_stat_activity` for `idle in transaction` |
| Requests hang, nothing in the logs | `py-spy dump --pid 1` | The stack says what it is blocked on |
| A container keeps restarting | `docker compose logs --tail 100` | Usually config or a failed migration |
| Service is up but `/ready` is 503 | The `checks` object in the response body | It names the dependency |
| Orders stuck before CONFIRMED | `orders_by_status` panel | `runbooks/orders-stuck-pending.md` |
| Memory climbing over hours | `docker stats` | `tracemalloc` snapshot comparison |
| Cache seems stale or useless | `redis-cli info stats` | Compare `keyspace_hits` and `keyspace_misses` |
| Background work not happening | `docker compose ps worker beat` | `redis-cli -n 1 llen celery` |
| You need to see a variable at runtime | debugpy attach | `DEBUG_ATTACH=1`, then VS Code |

## logtool

The whole reason for structured logs. Reads `logs/*.log`.

```powershell
.venv\Scripts\python.exe tools\logtool.py trace <request-id>
.venv\Scripts\python.exe tools\logtool.py errors --since 15m
.venv\Scripts\python.exe tools\logtool.py slow --top 10 --since 15m
.venv\Scripts\python.exe tools\logtool.py order <order-id>
```

`trace` is the one that earns its keep. It prints every line for a request id across all
three services in time order, with the gap in milliseconds since the previous line — so a
slow hop is obvious without doing arithmetic on timestamps:

```
18:19:35.008      +40ms  inventory  info     stock_reserved        order_id=3f6b... items=2
18:19:35.058      +35ms  payments   info     gateway_call          order_id=3f6b... latency_ms=612
18:19:35.678     +620ms  payments   warning  payment_declined      error_code=CARD_DECLINED
18:19:35.728      +45ms  inventory  info     reservation_released  units=2
```

Every subcommand takes `--json` for piping somewhere else. Exit codes are 0 for success,
1 when nothing matched, 2 for a usage error, so it can be used in a script.

Half-written lines are counted and skipped rather than crashing the run — that happens
whenever you read a file a process is still appending to.

## Grafana and Prometheus

Grafana is on :3000, Prometheus on :9090.

Use the dashboard to find **where**, then the logs to find **what**. The dashboard cannot
tell you why a specific request failed; the logs cannot tell you that 4% of all requests
are failing. Going in the other order wastes time.

- http://localhost:9090/targets — is Prometheus actually scraping? A missing panel is
  usually a dead target, not a missing metric.
- http://localhost:9090/alerts — Pending means the condition is true but has not held for
  its `for:` duration yet.

The alert rules are in `monitoring/alerts.yml` and each one names a runbook.

## PostgreSQL

**`pg_stat_statements` — what has been expensive since the last reset.**

```powershell
docker compose exec postgres psql -U app -d orders_db -c "SELECT calls, round(mean_exec_time::numeric,2) AS avg_ms, round(total_exec_time::numeric,2) AS total_ms, left(query,90) FROM pg_stat_statements ORDER BY total_exec_time DESC LIMIT 10;"
```

Sort by `total_exec_time`, not `mean_exec_time`. A 20ms query run ten thousand times costs
more than a two second query run twice, and only one of them shows up if you sort by mean.

**`pg_stat_activity` — what is happening right now.**

```powershell
docker compose exec postgres psql -U app -d orders_db -c "SELECT pid, state, wait_event_type, wait_event, now()-query_start AS runtime, left(query,80) FROM pg_stat_activity WHERE state <> 'idle' ORDER BY runtime DESC;"
```

`wait_event_type = 'Lock'` means one transaction is waiting on another. `idle in
transaction` means somebody opened a transaction and wandered off, which holds both a
connection and its locks.

**`EXPLAIN ANALYZE` — why one query is slow.** `EXPLAIN` shows the plan; `EXPLAIN ANALYZE`
runs it and shows what really happened. Look for a `Seq Scan` on a table that should have
an index, and for a large gap between estimated and actual row counts — that gap means the
planner is working from bad statistics.

`EXPLAIN ANALYZE` on an `UPDATE` or `DELETE` **executes it**. Wrap it in a transaction and
roll back if you are on data you care about.

## Redis

```powershell
docker compose exec redis redis-cli info stats     # keyspace_hits vs keyspace_misses
docker compose exec redis redis-cli info memory    # used_memory_human, evicted_keys
docker compose exec redis redis-cli --scan --pattern "inventory:product:*" | Measure-Object
docker compose exec redis redis-cli -n 1 llen celery
```

The hit/miss ratio is the honest measure of whether the cache is doing anything. Rising
`evicted_keys` means the working set no longer fits.

**Never run `KEYS *` on anything you care about.** It is O(n) and blocks the single Redis
thread for the whole scan, so a debugging command becomes an outage. Use `--scan`.

`MONITOR` prints every command in real time. It is excellent for finding out what is
actually being cached, and it costs real throughput — turn it off again.

## Containers

```powershell
docker compose ps
docker compose logs -f --tail 50 orders
docker stats --no-stream
```

`docker stats` is where you see a memory leak as a rising RSS, and CPU saturation as a
container pinned near 100%.

## py-spy — a process that is hung or busy and says nothing

The right tool when there are no logs, because the thing you want to know is where the
code is *stuck*, and a stuck process logs nothing by definition.

```powershell
docker compose exec orders pip install py-spy
docker compose exec orders py-spy dump --pid 1      # stack of every thread, right now
docker compose exec orders py-spy top --pid 1       # where CPU time is going
```

It attaches to a running process without restarting it and without any code change, so
the evidence survives. Read the dump before you restart anything — a restart fixes the
symptom and destroys the only copy of the cause.

A dump full of `psycopg` waits means the database or the pool. Full of `httpx` waits means
a downstream service. Spinning in our own code means a loop that does not terminate.

## tracemalloc — memory that grows and does not come back

`docker stats` tells you memory is climbing. `tracemalloc` tells you which lines allocated
it.

```python
import tracemalloc
tracemalloc.start(10)
first = tracemalloc.take_snapshot()
# ... let it run under load ...
second = tracemalloc.take_snapshot()
for stat in second.compare_to(first, "lineno")[:10]:
    print(stat)
```

Comparing two snapshots is the part that matters. A single snapshot shows what is
allocated, which is mostly normal; the difference between two shows what is *accumulating*,
which is the leak. Attach with debugpy and run it in the console against a live process.

## The debugger

```powershell
docker compose stop orders
$env:DEBUG_ATTACH = "1"
docker compose up -d orders
```

Then run **Attach: orders** in VS Code. Ports are 5678 orders, 5679 inventory, 5680
payments, 5681 worker. Add `DEBUG_WAIT=1` if the problem happens during startup — the
process then waits for you to attach before running anything.

Worth being honest about when this is the right tool. A debugger answers "what is the value
of this variable on this request, right now". It is the wrong tool for "which of ten
thousand requests failed" — that is logtool — and for anything intermittent, because you
cannot breakpoint your way to a race that happens once an hour. Reach for it when you can
reproduce the problem on demand and you need to see state that is not in the logs.
