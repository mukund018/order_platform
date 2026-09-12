To: Platform / whoever owns the orders-service deployment configuration
Cc: Inventory team (for awareness — **you are not being paged, see below**)
Severity: SEV2 | Started: ~08:47 UTC | Ongoing: yes at time of writing

## Summary

orders-service is abandoning calls to inventory-service after 250ms. Inventory is healthy
and is answering those same calls successfully — we are hanging up on it. `INVENTORY_TIMEOUT_S`
is `0.25` in the running environment; `.env.example` documents `2.0`.

## Impact

Measured over a 3-minute window, 08:47:41–08:50:42 UTC:

- **1051** `POST /orders` attempts, **22** returned 504 → **2.1%** of checkouts failed
- **23** `upstream_timeout` events, 9 of them on `POST /reservations`
- **9** orders left in `PENDING` and still there — these do not self-heal for 15 minutes
- **18** reservation rows `ACTIVE`, **38 units** of stock held against orders that cannot
  complete and are therefore unsellable until the expiry job runs

Customer-visible effect is the one in the ticket: checkout spins, then errors, and the
order shows as Pending. Retrying often works, which is why the reported rate is higher than
the failure rate — customers are retrying.

## Evidence

One request, both sides, `request_id 7a8642b8b9f1442d934994b78fb77891`:

```
08:49:43.689  orders     upstream_timeout  upstream=inventory duration_ms=252.78 timeout_s=0.25
08:49:43.694  orders     http_request      POST /orders status_code=504 duration_ms=264.09
08:49:43.721  inventory  http_request      GET /products/{sku} status_code=200 duration_ms=159.82
```

inventory returned 200 for the request orders recorded as a timeout, and logged it 28ms
*after* the customer had already been told the order failed.

Inventory-service's own latency across the same window, from its access logs: 5188
requests, p50 10ms, p95 33ms, p99 117ms, zero 5xx. **This is not an inventory problem and
the inventory team should not be woken up.** The 250ms budget is smaller than
client-observed latency (which includes queueing before the server starts handling), not
smaller than inventory's service time.

## Ruled out

- A deployment — no code change in the window
- inventory-service health — `/ready` green, zero 5xx, p95 33ms
- payments-service — unrelated, only the usual declines
- PostgreSQL and Redis — every `/ready` dependency check `ok`
- Genuine out-of-stock — real but produces FAILED orders with a reason, not 504s

## Ask

1. **Confirm who changed `INVENTORY_TIMEOUT_S` to `0.25` and when.** I can mitigate without
   this, but we need to know whether it was deliberate and whether the same change went
   anywhere else.
2. **Approve reverting it to 2.0** and recreating the orders container. Expected to stop
   new failures within a minute.
3. After mitigation: the 9 stuck PENDING orders will clear on the expiry job's next passes.
   I will confirm the ACTIVE reservation count returns to zero rather than assuming it.

## What I am doing meanwhile

Reverting the value in the working environment and recreating orders-service. I will
confirm from the logs that `upstream_timeout` stops rather than declaring it fixed.
