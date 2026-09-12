# Runbook — customer charged, order recorded FAILED

**Symptom:** a customer says their bank shows a charge, but their order in the app says
Failed and no confirmation arrived. Usually surfaces as a handful of support tickets
before it surfaces as an alert.

## Why this happens

`orders-service` waits up to `PAYMENTS_TIMEOUT_S` for `payments-service` to answer a
charge request. A **client-side timeout only means the caller stopped waiting** — it does
not stop the request on the other end. If `payments-service`'s own processing (in
practice, the simulated gateway's latency) can legitimately take longer than
`PAYMENTS_TIMEOUT_S`, some fraction of calls will time out from `orders-service`'s side
while the gateway goes on to complete the charge successfully a moment later. The order
is marked FAILED (correctly, from what `orders-service` could see) and the reservation is
released — but the customer was, in fact, charged.

This is what INC-004 was: `GATEWAY_LATENCY_MS_MAX` was configured above
`PAYMENTS_TIMEOUT_S`, so roughly `(gateway_max - timeout) / (gateway_max - gateway_min)`
of all charges hit exactly this window.

## Confirm it

**Check the alert first** — `PaymentReconciliationMismatch` fires on any nonzero rate of
`payment_reconciliation_mismatch_total`, which the `reconcile_payment_mismatches` beat
task (every 15 minutes) computes directly: it finds orders FAILED with
`failure_reason = "payments did not answer within the timeout"` and asks
payments-service whether it actually completed that charge.

To reconcile by hand, or to check a wider window than the task's default 24 hours:

```powershell
docker compose exec postgres psql -U app -d orders_db -c "
SELECT id, total_paise, created_at FROM orders
WHERE status = 'FAILED' AND failure_reason = 'payments did not answer within the timeout'
ORDER BY created_at DESC;"
```

For each order id, check its actual payment:

```powershell
curl.exe http://localhost:8001/orders/<order-id>   # via the API
```

or directly:

```powershell
docker compose exec postgres psql -U app -d payments_db -c "
SELECT status, amount_paise, provider_ref FROM payments WHERE order_id = '<order-id>';"
```

`status = SUCCEEDED` on a payment whose order is FAILED is the mismatch.

**Check the config that causes it:**

```powershell
docker compose exec payments env | grep GATEWAY_LATENCY
docker compose exec orders env | grep PAYMENTS_TIMEOUT
```

If `GATEWAY_LATENCY_MS_MAX` (payments) is not comfortably below `PAYMENTS_TIMEOUT_S`
(orders, in milliseconds) with headroom, this will keep happening. `payments-service`
refuses to boot if `GATEWAY_LATENCY_MS_MAX` exceeds `MAX_REALISTIC_GATEWAY_LATENCY_MS`
(`services/payments/app/config.py`) — so this specific misconfiguration is now caught at
startup, not from a finance escalation.

## Mitigation

Fix the config drift (revert `GATEWAY_LATENCY_MS_MAX`, or raise `PAYMENTS_TIMEOUT_S` with
real headroom — the two must stay compatible) and restart `payments`. This stops *new*
mismatches; it does nothing for orders already affected.

## What this does not do automatically, on purpose

Reconciliation never changes an order's status. By the time a mismatch is found, the
stock that order reserved has already been released and may belong to a different order
— silently flipping the order back to CONFIRMED risks an oversell on top of the original
problem. The correct next step (refund, manual fulfilment, goodwill credit) depends on
facts the system cannot see (is the item still available? has the customer already been
refunded some other way?), so this is a human decision. The reconciliation task's job is
only to make sure that decision gets made *at all*, and quickly.

## Prevention

- `services/payments/app/config.py`'s `MAX_REALISTIC_GATEWAY_LATENCY_MS` guard.
- `reconcile_payment_mismatches` (orders-service beat schedule, every 15 minutes) plus
  the `PaymentReconciliationMismatch` alert.
- Still open: `orders.failure_reason` is free text, matched against a hardcoded string
  (`PAYMENT_TIMEOUT_FAILURE_REASON` in `app/service.py`) because there is no dedicated
  failure-code column. A real `failure_code` column would make this match structurally
  instead of by string comparison.
