# Investigation — INC-011

Severity: SEV2 — a revenue event (a promo) failing for ~5% of customers, reproducible on
demand, self-resolving the moment load drops.
Impact: checkout failing for roughly 1 in 20 attempts during the promo, worse the more
concurrent multi-item orders overlap on the hot SKU.
Acknowledged at: 2026-09-12T15:20:00Z

**Fast-track note:** resolved via `chaos.py reveal --force` rather than a blind
investigation, per Kumar's instruction. 0 hypotheses tested before the answer was known.

## What was found (via `reveal --force`)

`services/inventory/app/service.py`'s `reserve()` iterated reservation lines in
whatever order the request listed them, instead of a fixed SKU order — the comment
explaining why ("Two orders holding the same two products in opposite order would
otherwise be able to deadlock each other") had been removed along with the `sorted()`
call it justified. Each line takes a row lock on its product; two concurrent multi-item
orders that share two products and lock them in opposite order each hold one row and
wait for the other. Postgres detects the cycle and kills one transaction with a deadlock
error, which surfaces as a fast 500 — fast, not slow, because a deadlock is a kill, not a
wait. A hot SKU (the promo) sharply raises the odds that two simultaneous baskets overlap
on two products, which is why it only showed up once traffic hit the promoted item.

Notably, the *release* path (`_in_sku_order`, used by `release_reservation`) was never
touched and already sorts for the same reason — only the reserve path's ordering was
removed.

## Fix and verification

Restored `sorted(items, key=lambda item: item.sku)` and its explanatory comment.

Added a real concurrency regression test
(`services/inventory/tests/test_concurrency.py::test_opposite_order_multi_item_reservations_do_not_deadlock`):
15 pairs of threads (30 total), each pair reserving the same two products in opposite
order, released simultaneously via a barrier to maximise collision odds. **Verified the
test actually catches the bug, not just that it passes**: temporarily reverted the fix,
re-ran the test, and it failed with a real Postgres `deadlock detected` error — then
restored the fix and confirmed all 53 inventory tests pass against real Postgres.
