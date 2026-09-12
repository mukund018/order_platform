# Investigation — INC-009

Severity: SEV3 — no errors, no outage, but real sellable stock invisible to the storefront
and orders being refused that could have been fulfilled.
Impact: 5 of 8 spot-checked SKUs under-reporting available stock by 4–30 units each,
some for several days.
Acknowledged at: 2026-09-12T15:03:00Z

**Fast-track note:** resolved via `chaos.py reveal --force` rather than a blind
investigation, per Kumar's instruction. 0 hypotheses tested before the answer was known.

## What was found (via `reveal --force`)

`stock_reservations` rows left `ACTIVE` for orders that either don't exist in `orders_db`
at all, or have already reached a terminal status — "the state a half-finished manual
data fix leaves behind." Stock is only returned when a reservation is `RELEASED`, and
nothing revisits an `ACTIVE` row unless something asks: the expiry job only looks at
`PENDING`/`RESERVED` *orders*, and an `ACTIVE` reservation with no live order behind it
is invisible to it.

Built the reconciliation the reveal describes as "the whole investigation" — a join
between inventory's `ACTIVE` reservations and orders-service's own status for each
`order_id` — as a real tool (`tools/reconcile_stock.py`), backed by a new inventory
endpoint (`GET /reservations/active`, since no way to list them existed before). Ran it
against the live stack:

```
15 stranded reservation(s) across 5 sku(s):
  SKU-0001: 19 units stranded
  SKU-0002: 27 units stranded
  SKU-0003: 11 units stranded
  SKU-0004: 22 units stranded
  SKU-0005: 17 units stranded
```

Five SKUs affected, matching the warehouse's "five of eight lines" exactly. All 15
reservations pointed at `order_id`s with no matching row in `orders_db` at all —
confirmed via the tool reporting `order_status: MISSING` for every one, not a mix of
terminal statuses. `created_at` on the stranded rows was 2026-09-09, several days before
this incident — matching "some of them have been on 1 for days."

## Fix and verification

Released the 15 stranded reservations via the real API
(`tools/reconcile_stock.py --release`, which calls inventory's existing, idempotent
`POST /reservations/{order_id}/release` for each) — chosen over touching the database
directly so the same code path (and the same cache invalidation) that every legitimate
release goes through is what ran here too. Verified: a second run of the tool found zero
remaining stranded reservations, and `SKU-0001`'s stock reading jumped from the
pre-release count to reflect all 19 returned units.
