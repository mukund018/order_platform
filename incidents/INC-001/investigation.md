# Investigation — INC-001

Severity: **SEV2** — checkout is failing for a measurable share of customers and every
failure leaves stock reserved against an order that will never complete. Not SEV1,
because most orders still succeed and browsing is unaffected.

Impact: about 2% of checkout attempts return 504, and each one leaves an order stuck in
PENDING holding stock that only the expiry job will release, fifteen minutes later.

Acknowledged at: 08:48 UTC (ticket raised 09:14 in the reporter's local time; the UTC
clock below is the one the logs and the database use, and it is the only one I trust).

## Triage notes

The reporter gave three facts worth more than the rest of the ticket:

1. Orders are *there* and sitting on Pending. So requests are reaching orders-service and
   it is writing rows. This is not an outage and not a routing problem.
2. Product pages are quick. So inventory-service is serving reads fine for browsers.
3. Nobody deployed. Worth believing provisionally, but "nobody deployed" only rules out a
   code change — it does not rule out a configuration change.

Fact 1 plus fact 2 already constrains this a lot: something between orders-service and
one of its dependencies is failing, while the dependency itself looks healthy to everyone
else.

## Log

| Time | Hypothesis | Tool / action | Finding | Result |
|---|---|---|---|---|
| 08:48 | Is anything actually down? | `curl /ready` on all three, ops console | orders, inventory, payments all `ready`, every dependency check `ok` | ruled out |
| 08:48 | How bad is it, in numbers? | `psql orders_db`, orders by status over the last 20 min | 944 CONFIRMED, 285 FAILED, **9 PENDING** | quantified |
| 08:49 | Are the PENDING orders really stuck, or just in flight? | same query, filtered on `created_at` | all 9 older than several minutes — nothing takes minutes in the happy path | **confirmed stuck** |
| 08:49 | Is stock being stranded by them? | `psql inventory_db`, `stock_reservations` where `status='ACTIVE'` | 18 rows, 38 units held | confirmed, and this is the business cost |
| 08:49 | What is the actual error? | `logtool errors --since 10m` | 23 × `orders / UPSTREAM_TIMEOUT`, and 23 × `orders / upstream_timeout` carrying `upstream=inventory ... duration_ms=252.78 timeout_s=0.25` | **lead** |
| 08:50 | Is inventory slow? | `logtool errors`, inventory rows | inventory logs **zero** 5xx of its own. Its only errors are `OUT_OF_STOCK` 409s, which are a business answer, not a fault | ruled out |
| 08:50 | Confirm it: what does inventory think its own latency is? | parsed `duration_ms` off every inventory `http_request` in the window | 5188 requests, **p50 10ms, p95 33ms, p99 117ms**. Only 12 requests (0.2%) took longer than 250ms | inventory is fast |
| 08:50 | Then who decided 250ms was the limit? | `logtool trace` on one failing request id | see below — the decisive one | **root cause** |
| 08:51 | Is the timeout value in the running config? | `grep INVENTORY_TIMEOUT_S .env` | `INVENTORY_TIMEOUT_S=0.25`. `.env.example`, which is the documented default, says `2.0` | **confirmed** |

## The decisive evidence

One request id, seen from both sides, via `logtool trace 7a8642b8b9f1442d934994b78fb77891`:

```
08:49:43.689        orders     error  upstream_timeout   upstream=inventory method=GET
                                                         path=/products/SKU-0032
                                                         duration_ms=252.78 timeout_s=0.25
08:49:43.692  +3ms  orders     error  request_failed     error_code=UPSTREAM_TIMEOUT status_code=504
08:49:43.694  +1ms  orders     info   http_request       method=POST path=/orders status_code=504
08:49:43.721 +28ms  inventory  info   http_request       method=GET path=/products/{sku}
                                                         status_code=200 duration_ms=159.82
```

Read the last line carefully. For the request orders-service reported as a timeout,
inventory-service recorded a **200 in 159.82ms** — and it finished 28ms *after* orders had
already given up and answered the customer with a 504.

Two services cannot disagree about whether a call succeeded unless the caller stopped
listening. That is what a client-side timeout is.

## Why 250ms is enough to break it when inventory answers in 33ms

This is the part worth being precise about, because the obvious explanation is wrong.

The obvious story would be "the timeout was set below the dependency's p95". It was not:
inventory's p95 is 33ms and its p99 is 117ms, both comfortably inside 250ms. Only 0.2% of
its requests exceed the budget by its own measurement.

But the timeout does not measure what the server measures. Inventory's `duration_ms` starts
when uvicorn begins handling the request. The client's 252.78ms covers connection
acquisition, the network hop, **time queued before the server picks the request up**, the
handling, and the response. In the trace above, inventory spent 159.82ms handling a request
the client had been waiting 252.78ms for — so roughly 93ms went somewhere before inventory
started counting, on a request that was already unusually slow to handle.

Under 12 rps that queueing is normal and invisible. With a 2.0s budget nobody ever sees it.
With a 0.25s budget it turns into a 2% checkout failure rate.

## Why a timeout produces a *stuck* order rather than a failed one

Nine of the 23 timeouts were on `POST /reservations` rather than on pricing. That path is
deliberately different: `services/orders/app/service.py` leaves the order `PENDING` on a
reservation timeout instead of failing it, because a timeout does not tell us whether
inventory took the stock or not. Failing the order would risk releasing stock that was
never reserved; leaving it PENDING hands it to the expiry job, which releases safely.

So the design is behaving correctly. It is the reason the symptom the customer described —
orders that sit on Pending forever — looks so different from a plain error, and it is why
the stuck orders and the 504s are the same incident rather than two.

## Ruled out, with the evidence for each

- **A deploy.** No code change; `git log` on main is unchanged since before the window.
- **inventory-service being unhealthy.** Zero 5xx from it, p95 33ms, `/ready` green.
- **payments.** No payment errors in the window beyond the expected declines.
- **Database or Redis.** Every `/ready` dependency check reports `ok`.
- **Genuine out-of-stock.** 236 `OUT_OF_STOCK` events exist and are real, but they produce
  `FAILED` orders with a reason, not 504s and not stuck PENDING ones. Different symptom,
  different cause, and separating the two was most of the work.

## Conclusion

`INVENTORY_TIMEOUT_S` is `0.25` in the running configuration; the documented default is
`2.0`. Every call from orders-service to inventory-service now has a 250ms budget that
includes queueing the caller cannot see, so the slowest ~2% of them are abandoned while
inventory is still successfully serving them.
