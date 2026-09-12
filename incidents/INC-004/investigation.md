# Investigation — INC-004

Severity: SEV2 — money at risk and customers double-charged (or at risk of it), matching
the severity matrix exactly ("30% of orders failing, customers double-charged"). Not SEV1:
most orders still complete correctly, and this is a data-consistency/reconciliation
problem, not a full outage.
Impact: ~1 in 3-4 orders affected per the ticket; customers charged but shown FAILED, some
retrying and risking a second charge; finance exposure unknown until reconciled.
Acknowledged at: 2026-09-12T13:53:33Z

| Time (UTC) | Hypothesis | Tool / action | Finding | Result |
|---|---|---|---|---|
| 13:53 | Config drift on the two timeout-related values this class of bug always turns out to be (see INC-001, decision #9 in `docs/decisions.md`) | `docker exec order-platform-orders-1 env \| grep TIMEOUT` and `docker exec order-platform-payments-1 env \| grep GATEWAY` | `PAYMENTS_TIMEOUT_S=3.0` (orders), but `GATEWAY_LATENCY_MS_MAX=4200` (payments) — the simulated gateway can take 4.2s, 1.2s longer than orders is willing to wait | **found on the first check** |
| 13:54 | Confirm the failure rate actually matches this math: P(gateway latency > 3000ms) over a uniform [50, 4200] draw ≈ (4200-3000)/(4200-50) ≈ 28.9% | `traffic.py --rps 10 --duration 600 --mix 0.3`, then `orders` status breakdown | First pass showed ~86-99% FAILED — far higher than predicted. Investigated further below before trusting the number. | inconsistent with the theory — did not immediately abandon it |
| 13:55 | Is the high failure rate actually from stock exhaustion carried over from INC-001/002/003's testing today, not from this fault? | `failure_reason` breakdown on FAILED orders | Confirmed: dominant reason was "`<SKU> has 0 units, N requested`" — the catalogue was legitimately sold out from cumulative testing, unrelated to this incident | confirmed as contamination, not signal |
| 13:56 | Restock via the real API (`PATCH /products/{sku}/stock`, not raw SQL) and re-measure on a clean signal | restocked all 50 products to 500 units each via `PATCH`, then re-ran the same status/failure_reason breakdown on orders created after the restock | 249 CONFIRMED, 100 FAILED, 12 in-flight ≈ **27.7% failure rate** — matches the predicted ~28.9% almost exactly. Failure reasons: 117/125 "payments did not answer within the timeout", 8/125 ordinary declines (matches the normal 5% `GATEWAY_FAILURE_RATE` baseline) | **confirmed**: this is the mechanism |
| 13:58 | The critical question the ticket actually asks: were these customers really charged? | Reconciled `orders_db` (FAILED, timeout reason) against `payments_db` (by `order_id`) directly via a short script, not a single query — the two tables live in different databases with no cross-database join | **148 of 156 timed-out orders had a `SUCCEEDED` payment row** — total ₹15,32,637 charged against orders recorded as FAILED | root cause confirmed with real numbers |

## Notes

**No hints used.** Found via the same method as INC-001: check the actual running
configuration on both sides of the call, not just one. `PAYMENTS_TIMEOUT_S` alone looked
unremarkable (3.0s is the documented default); the fault only becomes visible by also
checking what the *downstream* service was configured to simulate.

**A real methodological trap, worth keeping in the runbook:** the first attempt to
measure the failure rate came back wildly wrong (85%+, not ~29%) because the product
catalogue had been driven to near-zero stock by three earlier incidents' worth of load
testing earlier in this same session, and `SKU-000X has 0 units` failures were drowning
out the actual signal. Restocking through the real API before re-measuring is what
produced a number that actually matched the predicted math — a reminder to check *why*
something is failing, not just *how much*, before trusting a rate.

**Hints used: 0.** Recorded honestly per this session's rule: these incidents are being
closed by the AI at Kumar's explicit request, not diagnosed blind by him.
