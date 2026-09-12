# One service is timing out calls to another

**Alert:** `UpstreamTimeouts` — `rate(upstream_calls_total{outcome="timeout"}[5m]) > 0` for 2m
**Typical severity:** SEV2. Checkout fails for a share of customers, and a reservation
timeout leaves stock held by an order that cannot complete.
**First seen:** INC-001

## What the symptom looks like

- A trickle of 504s on `POST /orders` — often only a few percent, not an outage
- Orders sitting in `PENDING` and not moving
- The dependency looks completely healthy on its own dashboards
- Customers report "it failed, I tried again and it worked"

## The one question that splits this in two

**Is the dependency actually slow, or has the caller stopped waiting?**

These look identical from the caller and need opposite responses. Answer it before you
escalate to anyone, because half the time the team you are about to page has nothing wrong
with their service.

```bash
# Pick any failing request id - from the error response body, the errors panel, or:
grep -h upstream_timeout logs/orders.log | tail -1

python tools/logtool.py trace <request_id>
```

Read both sides of the same request id:

| What you see | What it means | Go to |
|---|---|---|
| The dependency logged the **same request id** as a 2xx, at or after the moment the caller gave up | The caller hung up on a healthy service. **Client-side timeout.** | Section A |
| The dependency has **no line at all** for that request id | It never got the request, or died holding it | Section B |
| The dependency logged it as slow (its own `duration_ms` near or above the budget) or as a 5xx | It really is struggling | Section C |

Also compare the two numbers directly:

```bash
# What the caller was willing to wait, and what it actually waited
grep -h upstream_timeout logs/orders.log | tail -5 \
  | python -c "import sys,json; [print(json.loads(l)['timeout_s'], json.loads(l)['duration_ms']) for l in sys.stdin]"
```

## Section A — the caller stopped waiting (client-side timeout)

Most common, and the one INC-001 was.

1. **Check the configured budget against the documented default.**
   ```bash
   grep -E "INVENTORY_TIMEOUT_S|PAYMENTS_TIMEOUT_S" .env .env.example
   docker compose config | grep -i timeout      # the config that is actually running
   ```
2. **Check the dependency's own latency**, so you can say it is healthy rather than assume:
   ```bash
   python tools/logtool.py slow --since 15m --top 10
   ```
   Or the p95 panel for that service in Grafana.
3. **Remember the two numbers are not the same thing.** The server's `duration_ms` starts
   when it picks the request up. The client's covers connection acquisition and time queued
   in front of the server as well. A dependency with a p95 of 33ms can still blow a 250ms
   client budget under load. Budget against the client's number, not the server's.
4. **Mitigate:** restore the timeout and recreate the caller.
   ```bash
   docker compose up -d --force-recreate orders
   ```
5. **Verify rather than declare.** Re-run the same load and confirm the timeouts stop:
   ```bash
   python tools/traffic.py --rps 12 --duration 120
   python tools/logtool.py errors --since 3m      # expect no UPSTREAM_TIMEOUT
   ```

Note: orders-service will now refuse to start if an outbound timeout is below
`MIN_UPSTREAM_TIMEOUT_S` (0.5s). If the container is in a crash loop with a pydantic
`greater_than_equal` error naming a timeout setting, that guard is why — and it has already
told you the answer.

## Section B — the request never arrived

- Is the dependency up and reachable? `docker compose ps`, then its `/ready`
- Connection pool or socket exhaustion on either side — see `runbooks/db-pool-exhausted.md`
- Anything in front of them (nginx, the docker network) dropping connections

## Section C — the dependency really is slow

Follow `runbooks/slow-requests.md` on the *dependency*, not on the caller. Briefly:

- Its own p95/p99 in Grafana, per endpoint
- `pg_stat_statements` in its database, ordered by `total_exec_time`
- `redis-cli INFO` if it uses the cache
- `py-spy dump` if it is busy and saying nothing

Then escalate to whoever owns it — with its own latency numbers, the request ids, and the
time window. An escalation without those gets handed straight back.

## Clearing up afterwards

A reservation timeout leaves the order `PENDING` **on purpose** — a timeout does not tell
us whether the stock was taken, so failing the order could release stock that is genuinely
reserved. Do not fix those by hand.

```sql
-- orders_db: what is still stuck
SELECT status, count(*) FROM orders
 WHERE status IN ('PENDING','RESERVED') AND created_at > now() - interval '1 hour'
 GROUP BY status;

-- inventory_db: stock those orders are holding
SELECT count(*), sum(qty) FROM stock_reservations WHERE status = 'ACTIVE';
```

The beat job sweeps them at `ORDER_EXPIRY_MINUTES` (15 by default), releasing the
reservation before marking the order EXPIRED, and refusing to mark it terminal if the
release fails. Wait for it and then confirm the ACTIVE count returns to its baseline. If
orders are still stuck well past the expiry window, that is a different incident —
`runbooks/orders-stuck-pending.md`.

## Prevention checklist

- [ ] Is the timeout above the dependency's client-observed p99, with headroom?
- [ ] Does the `UpstreamTimeouts` alert cover this caller/dependency pair?
- [ ] Did the change that set this value get reviewed against measured latency?
