# Investigation — INC-006

Severity: SEV3 — a quiet, permanent revenue leak (one stranded unit per SKU, forever),
not an outage or a customer-facing error storm.
Impact: eleven products observed stuck on exactly 1 unit; every SKU in the catalogue
eventually stops selling its last unit once it reaches that count.
Acknowledged at: 2026-09-12T14:40:00Z

**Fast-track note:** resolved via `chaos.py reveal --force` rather than a blind
investigation, per Kumar's instruction to prioritize a fully working platform for the
remaining incidents. 0 hypotheses tested before the answer was known.

## What was found (via `reveal --force`)

`services/inventory/app/service.py`'s `reserve()` used a conditional `UPDATE ... WHERE
stock > qty` instead of `stock >= qty`. The `>` guard rejects a request for *exactly* the
remaining stock (matches zero rows → correctly-but-wrongly interpreted as out of stock),
while accepting anything strictly less. The error message this produces
(`"SKU-0007 has 1 units, 1 requested"`) is entirely accurate about the numbers, which is
exactly why it reads as a real refusal rather than a bug — the system is not confused,
it is confidently wrong.

## Fix and verification

Changed `Product.stock > item.qty` to `Product.stock >= item.qty`
(`services/inventory/app/service.py`). Added two boundary tests
(`services/inventory/tests/test_reservations.py`): reserving exactly the available stock
now succeeds and leaves the product at zero; reserving one more than available is still
correctly rejected.

Verified live, not just in the test suite: drove `SKU-0007` down to exactly 1 unit via the
real `PATCH /products/{sku}/stock` endpoint, placed a real order for that last unit
end to end, got `CONFIRMED`, and confirmed the product's stock read back as `0`
afterward.
