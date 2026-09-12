# Runbook — 5xx error rate above 5%

**Alert:** `HighServerErrorRate` · **Severity:** SEV2

More than 5% of requests to one service are being answered with a server error. Note that
this counts **5xx only**. A declined card and an out-of-stock order are 200s and 201s by
design, so they cannot set this alert off — if it is firing, something is genuinely
broken.

## First five minutes

The dashboard tells you where, the logs tell you what.

1. Grafana → **Order Platform** → *Error rate by service*. Which service, and did it step
   up at a moment or ramp gradually? A step usually means a deploy or a config change; a
   ramp usually means saturation.
2. Group the errors by code:

```powershell
.venv\Scripts\python.exe tools\logtool.py errors --since 15m
```

The output is grouped by service and `error_code`, with one example request id each.

## Reading the error code

| Code | Meaning | Where to look next |
|---|---|---|
| `UPSTREAM_TIMEOUT` | orders gave up waiting on inventory or payments | The downstream service's p95 panel. It is probably slow, not down. |
| `UPSTREAM_ERROR` | a downstream returned something unusable, or the connection failed | `docker compose ps` for that service; then its own logs. |
| `DEPENDENCY_UNAVAILABLE` | a service cannot reach PostgreSQL or Redis | `docker compose ps postgres redis` |
| `INTERNAL_ERROR` | an unhandled exception | The traceback is in the log line — see below. |

`INTERNAL_ERROR` is the one that means a real bug. Take an example request id and pull
the whole story:

```powershell
.venv\Scripts\python.exe tools\logtool.py trace <request-id>
```

The `unhandled_exception` line carries the full traceback in its `exception` field, and
the lines around it show what the request was doing when it blew up.

## Common causes, in rough order

- **A downstream is slow, not down.** orders times out, so orders reports the error, but
  the fault is one hop away. Always check the p95 of the *downstream* before blaming the
  service that is alerting.
- **Connection pool exhausted.** The *DB connection pool* panel shows `in_use` pinned at
  `db_pool_size` + overflow. Requests then queue and time out. See
  [db-pool-exhausted.md](db-pool-exhausted.md).
- **A bad deploy.** Errors start at a sharp edge. `docker compose ps` shows a recently
  created container.
- **Bad data.** One specific SKU or one order id keeps recurring in the examples. The
  error rate tracks how often that row is touched.

## Mitigation

Restore service first, understand second:

- Downstream slow → raise its timeout only if that actually helps; usually it just moves
  the queue. Prefer taking load off.
- Bad deploy → roll back the image.
- One poisonous row → correct the row.
- Pool exhausted → restart the affected service to drop the stuck connections, then fix
  what was holding them.

## Escalation

If the fault is in a component owned by another team, send: the service, the error code,
three example request ids with timestamps, the error rate and when it started, and what
you have already ruled out. Expect to be asked "which order ids" — have them ready.
