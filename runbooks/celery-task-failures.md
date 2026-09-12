# Runbook — celery tasks failing or not running

**Alert:** `CeleryTaskFailureRate` · **Severity:** SEV3, SEV2 if confirmations are not going out

More than 10% of background tasks are ending in failure. There are only three tasks, and
they fail for quite different reasons:

| Task | What it does | What its failure means |
|---|---|---|
| `send_confirmation` | writes a `notifications` row for a confirmed order | Customers are not being told their order went through |
| `expire_stale_orders` | sweeps old PENDING/RESERVED orders to EXPIRED | Stock stays held. See [orders-stuck-pending.md](orders-stuck-pending.md) |
| `daily_sales_report` | logs yesterday's IST numbers | The business gets no report. Annoying, not urgent |

## Is it failing, or not running at all?

These look identical on a dashboard that only counts failures — a task that never starts
never fails either. Check liveness first:

```powershell
docker compose ps worker beat
docker compose exec redis redis-cli -n 1 llen celery
```

A queue length that climbs and never drains means nothing is consuming. A queue length of
zero with no task metrics moving means nothing is being *published*.

```powershell
docker compose logs --tail 100 worker
docker compose logs --tail 50 beat
```

Beat is the scheduler and the worker is the executor. Losing beat means the periodic
tasks stop silently; losing the worker means everything queues up.

## Which task, and why

```powershell
.venv\Scripts\python.exe tools\logtool.py errors --since 30m
```

Worker logs are the same JSON as the services and carry the `request_id` of whatever
request enqueued the task, so a failing `send_confirmation` can be traced back to the
order that caused it:

```powershell
.venv\Scripts\python.exe tools\logtool.py order <order-id>
```

Common causes:

- **The database is unreachable from the worker.** The worker has its own connection pool
  and its own `.env`; it can be broken while the API is fine.
- **`send_confirmation` running against an order that is not CONFIRMED.** The task refuses
  and that is correct behaviour — but if it is happening constantly, something is
  enqueueing it too early.
- **Retry storm.** `send_confirmation` retries up to five times with backoff. One
  genuinely broken order can contribute five failures on its own, which skews the rate
  when total volume is low.
- **Broker full or evicting.** `docker compose exec redis redis-cli info memory` — if
  `maxmemory` has been reached and the policy is evicting, queued tasks are being thrown
  away. That is silent and nasty.

## Re-running a task by hand

The tasks are ordinary functions, so you can call one directly inside the worker
container to see the real traceback instead of a celery summary:

```powershell
docker compose exec worker python -c "from app.tasks import expire_stale_orders; expire_stale_orders()"
```

`send_confirmation` is safe to re-run: the unique constraint on
`notifications (order_id, kind)` makes a duplicate a no-op rather than a second email.

## Mitigation

Restart the worker. If the queue has a large backlog, watch `llen celery` fall — if it
does not, the worker is consuming and failing rather than consuming and succeeding, and a
restart will not help.

## Prevention

If this fires and the cause was "beat was not running", that is a monitoring gap: nothing
alerts on *absence* of task runs. An alert on
`rate(celery_tasks_total{task="expire_stale_orders"}[10m]) == 0` would have caught it.
