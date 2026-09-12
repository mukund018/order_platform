---
id: INC-004
title: customers charged for orders recorded FAILED — gateway latency ceiling exceeded orders-service's payment timeout
severity: SEV2
services: [orders, payments]
category: Dependency failure or slowness
detected_at: 2026-09-12T13:53:00Z
mitigated_at: 2026-09-12T14:05:00Z
resolved_at: 2026-09-12T14:10:32Z
time_to_acknowledge_min: 1
time_to_mitigate_min: 12
hints_used: 0
---

## Summary

`payments-service` was configured with `GATEWAY_LATENCY_MS_MAX=4200` (documented default:
300), while `orders-service`'s `PAYMENTS_TIMEOUT_S` stayed at its default, 3.0s. Whenever
the simulated gateway's random latency draw exceeded 3 seconds — roughly 29% of charges,
given a uniform draw over [50ms, 4200ms] — orders-service gave up waiting and recorded
the order FAILED with the reservation released. The gateway, unaware the caller had
already walked away, kept running and completed the charge successfully moments later.
The customer really was charged; the order simply never found out.

This is the exact scenario named as an open question in `docs/decisions.md` after M3:
"Payment timed out, but the gateway actually charged the customer. What happens in your
system?" The honest answer, until this incident, was: nothing does anything about it.

## Impact

Reconciling `orders_db` against `payments_db` directly (the two live in separate
databases with no cross-database join, so this required a short script, not a query)
found **148 of 156** orders marked FAILED for a payment timeout had, in fact, been
charged successfully — **₹15,32,637** held against customers who were told their order
did not go through. Several of these customers, per the ticket, had already been told by
support to re-order, putting them at risk of a second, genuine charge on top of the first.
A one-off manual reconciliation run against the live database after the fix found 191
further mismatches in the existing backlog (capped at the reconciliation task's 200-row
batch size — more remain to be worked through on subsequent runs).

## Timeline (UTC)

| Time | Event |
|---|---|
| 13:53 | Ticket received (support escalation, 9 complaints in an hour); acknowledged, triaged SEV2 |
| 13:53–13:54 | Checked both sides of the timeout pair directly (`docker exec ... env`) — found `GATEWAY_LATENCY_MS_MAX=4200` against `PAYMENTS_TIMEOUT_S=3.0` on the first check |
| 13:54–13:56 | Measured failure rate under load; first measurement (~86-99%) was contaminated by unrelated stock exhaustion from earlier incidents' testing today — restocked via the real API and re-measured: ~27.7%, matching the predicted ~28.9% |
| 13:58 | Reconciled `orders_db` against `payments_db` by `order_id`: 148 of 156 timed-out orders were actually charged |
| 14:05 | **Mitigated** — `GATEWAY_LATENCY_MS_MAX` reverted to 300 in `.env`; `payments-service` rebuilt with a new startup guard (`MAX_REALISTIC_GATEWAY_LATENCY_MS`) that refuses to boot with this misconfiguration again |
| 14:08 | Built `reconcile_payment_mismatches` — a new Celery beat task (every 15 minutes) that finds FAILED-on-timeout orders and checks payments-service for a completed charge, logging and counting every mismatch it finds, without touching order status |
| 14:10 | Verified live: manually triggered the task against the real backlog — found 191 real mismatches, all logged and counted on `payment_reconciliation_mismatch_total`. **Resolved.** |

## Root cause — 5 Whys

1. **Why was a customer charged for an order marked FAILED?** `payments-service`
   completed the charge after `orders-service` had already stopped waiting for the
   response and recorded the order as failed.
2. **Why did `orders-service` stop waiting?** Its `PAYMENTS_TIMEOUT_S` (3.0s) was shorter
   than the gateway's configured latency ceiling (`GATEWAY_LATENCY_MS_MAX=4200`, 4.2s).
3. **Why was the gateway configured to potentially take longer than any caller would
   wait?** Configuration drift — `GATEWAY_LATENCY_MS_MAX`'s documented default is 300ms;
   nothing in the running environment matched that default.
4. **Why didn't anything catch this before it reached customers?** Nothing validated the
   relationship between the two services' timeout-related settings — each service's own
   config was internally valid (a positive latency range; a positive timeout), and
   neither service can see the other's environment to cross-check it directly.
5. **Why does a client timeout produce a real, uncorrected charge instead of just a
   slow response?** Because a timeout is purely a client-side decision to stop waiting;
   it has no effect on work already in flight on the server. Nothing in the system was
   watching for the case where the client's "no" and the server's "yes" disagree — that
   is a reconciliation problem, and until this incident, there was no reconciliation.

## Resolution

**Mitigation** (14:05): `GATEWAY_LATENCY_MS_MAX` reverted to 300 in `.env`; `payments`
rebuilt and restarted. Verified `/health` and `/ready` both green.

**Permanent fix, two parts:**
1. `services/payments/app/config.py` now caps `GATEWAY_LATENCY_MS_MAX` at
   `MAX_REALISTIC_GATEWAY_LATENCY_MS` (2000ms — comfortable headroom under
   `PAYMENTS_TIMEOUT_S`'s 3.0s default). Payments-service refuses to boot if this is
   violated, the same principle as INC-001's `MIN_UPSTREAM_TIMEOUT_S` guard, applied to
   the other end of the same problem: a timeout pair that can only produce mismatched
   charges is a configuration error, and the right place to find that out is at startup.
2. **`reconcile_payment_mismatches`** (new): a Celery beat task, every 15 minutes, that
   finds orders FAILED on a payment timeout and asks payments-service whether the charge
   actually completed. It deliberately never changes order status — by the time it runs,
   the stock that order held may already belong to a different order, so auto-correcting
   to CONFIRMED risks an oversell on top of the original problem. It logs every mismatch
   loudly (`payment_reconciliation_mismatch`, with amount and provider ref) and counts it
   on `payment_reconciliation_mismatch_total`, so a human can act on the money question
   without the system quietly guessing.

## Prevention

- **`MAX_REALISTIC_GATEWAY_LATENCY_MS` guard** — this specific misconfiguration cannot
  reach a running container again.
- **`PaymentReconciliationMismatch` alert** — fires on any nonzero rate of the new
  counter, `for: 0m` (this is not a "wait and see" alert; one mismatch is real money).
- **Regression tests** (`services/orders/tests/test_reconciliation.py`, 6 cases) cover:
  a genuine mismatch is found and counted; order status is never touched; a payment that
  genuinely never happened is not a false positive; an unrelated failure reason is never
  even checked; the lookback window is respected; a mismatch is still found correctly
  alongside unrelated failures in the same run.
- **Still open:** `failure_reason` is free text matched against a hardcoded string
  (documented in the runbook and in `service.py`'s own comment) because `orders` has no
  dedicated failure-code column. A structured column would make this match reliably
  instead of by string comparison, and is worth doing if this class of fault recurs.
  Also open: the reconciliation task reports mismatches but does not yet integrate with
  a refund or ledger system — there is nowhere in this platform for "issue a refund" to
  go yet, which is its own, larger feature.

## Lessons learned

- **The load test's first failure-rate measurement was badly wrong, and knowing why
  mattered more than the number itself.** Three earlier incidents' worth of testing in
  the same session had quietly sold out most of the catalogue; the fix was restocking
  through the real API and re-measuring, not distrusting the theory. Always check *why*
  something is failing before trusting *how much*.
- **A client timeout is a statement about the client, not the server.** "We stopped
  waiting" and "the work stopped" are different facts, and conflating them is exactly how
  a correctly-implemented timeout produces an incorrect business outcome.
- **The fix that matters here is not a smarter timeout value — no timeout value makes
  this safe on its own.** It is the reconciliation job: something has to compare what the
  client concluded against what actually happened, because the two are allowed to
  disagree by design (that is what a timeout *is*), and only a check that survives the
  disagreement catches it.
