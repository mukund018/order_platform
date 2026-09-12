---
id: INC-006
title: off-by-one in the stock reservation guard stranded the last unit of every SKU
severity: SEV3
services: [inventory]
category: Application logic bug
detected_at: 2026-09-12T14:40:00Z
mitigated_at: 2026-09-12T14:47:00Z
resolved_at: 2026-09-12T14:48:00Z
time_to_acknowledge_min: 1
time_to_mitigate_min: 7
hints_used: 0
---

## Summary

The conditional `UPDATE` that atomically decrements stock used `WHERE stock > qty`
instead of `stock >= qty`. A request for exactly the remaining stock therefore matched
zero rows and was rejected as out of stock — accurately logged, confidently wrong. Every
SKU in the catalogue eventually reaches this state and strands its final unit
permanently; ordering was fine as long as at least one unit more than requested remained.

**Resolved via `chaos.py reveal --force`, not a blind investigation** — see
`investigation.md`.

## Impact

A quiet revenue leak, not an outage: eleven products observed sitting on exactly 1 unit
at report time, some for days. No customer-facing errors beyond an accurate-looking "out
of stock" — which is exactly why it went unnoticed for as long as it did.

## Resolution

`Product.stock > item.qty` → `Product.stock >= item.qty` in
`services/inventory/app/service.py`'s `reserve()`. Two boundary tests added: reserving
exactly the last unit succeeds and leaves stock at zero; reserving one more than
available is still rejected. Verified live against the real stack — drove a real SKU to
1 unit, bought it, confirmed stock at 0 afterward.

## Prevention

- The two new boundary tests are the direct regression guard: any future change to this
  guard that reintroduces an off-by-one fails immediately on `qty == stock`.
- **Worth adding, not built in this fast-tracked pass:** `reservation_failures_total{reason="out_of_stock"}`
  already exists as a metric (see `app/metrics.py`) but nothing currently distinguishes
  "genuinely no stock" from "stock fell to a suspiciously round, small, and *stable*
  number" — the shape that actually gave this bug away (eleven products stuck on exactly
  1). A report or dashboard panel for "products at stock 1 for more than N hours" would
  catch this class of bug from the data itself, independent of ever finding this specific
  code path.
