# Runbook — orders stuck before CONFIRMED

**Alert:** `OrdersStuckPending` · **Severity:** SEV2

More than ten orders have been `PENDING` or `RESERVED` for over five minutes. A healthy
order goes `PENDING → RESERVED → CONFIRMED` in well under a second, so anything sitting
in those states is an order that started and never finished.

This one matters because **it can fire with no errors anywhere**. Every request returned
200, every panel looks normal, and money is quietly not being taken. Do not wait for a
5xx to confirm it is real.

## First five minutes

Find out which state they are stuck in — it splits the problem in half:

```powershell
docker compose exec postgres psql -U app -d orders_db -c "SELECT status, count(*), min(created_at) AS oldest FROM orders WHERE created_at > now() - interval '2 hours' GROUP BY status ORDER BY 2 DESC;"
```

- **Stuck in `PENDING`** → the order was created but the reservation call never came back.
  Look at inventory.
- **Stuck in `RESERVED`** → stock is held but the payment call never came back. Look at
  payments. This is the worse one: that stock is unsellable until the order expires.

Then read the event trail of one of them. It shows exactly how far it got:

```powershell
docker compose exec postgres psql -U app -d orders_db -c "SELECT o.id, o.status, e.from_status, e.to_status, e.reason, e.request_id, e.created_at FROM orders o JOIN order_events e ON e.order_id = o.id WHERE o.status IN ('PENDING','RESERVED') ORDER BY o.created_at DESC, e.created_at LIMIT 20;"
```

Take a `request_id` from that output and trace the whole request across services:

```powershell
.venv\Scripts\python.exe tools\logtool.py trace <request-id>
```

## Causes, in rough order

**The service died mid-flight.** Orders are committed as `PENDING` *before* any outbound
call, on purpose, so a crash leaves a visible record rather than losing the order. A cluster
of stuck orders all created within the same few seconds points at a restart — check
`docker compose ps` for a recent container start time.

**A downstream is hanging.** If inventory or payments is answering slowly but not
failing, orders will be piling up in whichever state comes before that call. Cross-check
the p95 panel and [slow-requests.md](slow-requests.md).

**The expiry job is not running.** Stuck orders should be swept to `EXPIRED` within
`ORDER_EXPIRY_MINUTES`. If the count only ever grows, the worker or beat is not running:

```powershell
docker compose ps worker beat
docker compose logs --tail 50 beat
docker compose exec redis redis-cli -n 1 llen celery
```

A queue length that keeps climbing means tasks are being published and nothing is
consuming them. See [celery-task-failures.md](celery-task-failures.md).

## The part that is easy to miss

Every `RESERVED` order is holding stock. If they are not being released, inventory will
start reporting `OUT_OF_STOCK` for products that are physically in the warehouse, and
customers will be turned away for no reason. Check the gap:

```powershell
docker compose exec postgres psql -U app -d inventory_db -c "SELECT count(*) AS active_reservations, sum(qty) AS units_held FROM stock_reservations WHERE status = 'ACTIVE' AND created_at < now() - interval '15 minutes';"
```

Anything older than `ORDER_EXPIRY_MINUTES` is stock held against an order that no longer
exists. That is a data-integrity problem, not just a stuck-order problem.

## Mitigation

1. Get the worker running again — that alone drains the backlog through
   `expire_stale_orders`, which releases the stock as it goes.
2. If the worker is healthy but the backlog is old, run the sweep by hand:

```powershell
docker compose exec worker python -c "from app.tasks import expire_stale_orders; expire_stale_orders()"
```

3. Only release reservations directly in the database as a last resort, and only after
   confirming the matching order is not in flight. It is easy to release stock for an
   order that is about to be confirmed.

## Escalation

If orders are stuck in `RESERVED` and payments is the suspect, the payments team will want
order ids, the exact time window, and whether any of them actually got charged. Check
before you ask:

```powershell
docker compose exec postgres psql -U app -d payments_db -c "SELECT order_id, status, attempts, created_at FROM payments WHERE status = 'PENDING' AND created_at < now() - interval '5 minutes';"
```

A `PENDING` payments row older than a few minutes means we asked the gateway and never got
an answer. Whether the customer was charged is then an open question — see decision 9 in
`docs/decisions.md`.
