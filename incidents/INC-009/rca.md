---
id: INC-009
title: stock stranded by ACTIVE reservations left behind for orders that no longer exist
severity: SEV3
services: [inventory, orders]
category: Data integrity across services
detected_at: 2026-09-12T15:03:00Z
mitigated_at: 2026-09-12T15:10:00Z
resolved_at: 2026-09-12T15:14:00Z
time_to_acknowledge_min: 1
time_to_mitigate_min: 7
hints_used: 0
---

## Summary

15 `stock_reservations` rows across 5 SKUs were left `ACTIVE` for `order_id`s that do
not exist in `orders_db` at all — "the state a half-finished manual data fix leaves
behind," per the injected fault's own description. Stock is only returned to a product
when its reservation is explicitly `RELEASED`; nothing in the platform revisits an
`ACTIVE` reservation whose order is gone, because the expiry job (the only thing that
sweeps stale reservations) selects on order status, and there is no order row to select.
The result is a constant, silent offset between what inventory reports as available and
what physically exists — orders being refused that could have been fulfilled, with
nothing erroring anywhere.

**Resolved via `chaos.py reveal --force`, not a blind investigation** — see
`investigation.md`. The reconciliation query the reveal names as "the whole
investigation" was built as a real, reusable tool rather than a one-off query.

## Impact

5 of 8 spot-checked SKUs affected, 4–30 stranded units each (19/27/11/22/17 across the
five), some stranded for several days before detection. Revenue-neutral in the sense that
nothing was oversold — this is refused-but-fulfillable demand, the opposite failure mode
from an oversell.

## Resolution

Built `tools/reconcile_stock.py` (cross-service, HTTP-only, matching this project's
existing tools/ conventions — no direct database access from a tool): it lists every
`ACTIVE` reservation from a new `GET /reservations/active` endpoint on inventory-service
(nothing previously exposed this), checks each `order_id` against orders-service's
existing `GET /orders/{id}`, and reports any reservation whose order is terminal *or
missing entirely*. Released the 15 found via `--release`, which drives the same
idempotent `POST /reservations/{order_id}/release` endpoint (and the same cache
invalidation) that every legitimate release already goes through, rather than writing to
the database directly. Verified: a second run found zero remaining stranded reservations,
and the affected products' stock readings reflected every returned unit.

One real bug caught and fixed in the tool itself before this was done: the first
implementation excluded `MISSING`-order reservations from `--release` as an overcautious
safety check, which silently released 0 of the 15 stranded rows on the first attempt.
There is no scenario where leaving stock stranded against an order that provably does not
exist is the safer choice, so the exclusion was wrong, not merely conservative — fixed
before landing.

## Prevention

- Two new tests (`services/inventory/tests/test_reservations.py`,
  `tools/tests/test_reconcile_stock.py`) cover: the new endpoint returns only truly
  `ACTIVE` rows (not `COMMITTED`/`RELEASED`); the reconciliation logic correctly
  classifies a terminal order, a live order, and a missing order.
- **Not built in this fast-tracked pass:** `tools/reconcile_stock.py` is currently a
  manual/on-demand tool, not a scheduled job with an alert — the reveal's own recommended
  prevention ("a scheduled consistency check with an alert... nothing was watching that
  number") is only half done. The natural next step is the same shape as INC-004's
  `reconcile_payment_mismatches`: a Celery beat task wrapping this same check, with a
  metric and an alert, rather than something a person has to remember to run.
