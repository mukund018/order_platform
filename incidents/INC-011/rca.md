---
id: INC-011
title: reservation lines locked in request order instead of SKU order, deadlocking concurrent multi-item orders
severity: SEV2
services: [inventory]
category: Concurrency / race condition
detected_at: 2026-09-12T15:20:00Z
mitigated_at: 2026-09-12T15:26:00Z
resolved_at: 2026-09-12T15:30:00Z
time_to_acknowledge_min: 1
time_to_mitigate_min: 6
hints_used: 0
---

## Summary

`reserve()` locked a multi-item reservation's product rows in whatever order the caller
listed them, instead of a fixed SKU order. Each line's conditional `UPDATE` takes a row
lock; two concurrent orders sharing two products, locking them in opposite order, each
end up holding one lock and waiting on the other. Postgres detects the resulting cycle
and kills one transaction with a deadlock error — a fast failure, not a slow one, which
is the fingerprint that distinguishes a deadlock kill from ordinary lock-wait contention.
A promoted ("hot") SKU sharply raises the odds that two simultaneous baskets overlap on
two products, which is why the failure only appeared once traffic concentrated on one
item.

**Resolved via `chaos.py reveal --force`, not a blind investigation** — see
`investigation.md`.

## Impact

~5% of checkout attempts failing during the promo window, self-resolving whenever the
hot SKU was pulled and recurring the moment it returned — a real revenue event
materially degraded for a business-visible window, not an edge case.

## Resolution

Restored the fixed SKU-order locking (`sorted(items, key=lambda item: item.sku)`) and
the comment explaining why it exists. The corresponding release path was never affected
— it already sorts for the same reason and was left alone.

Verified thoroughly, not just re-applied: added a concurrency regression test (15 pairs
of threads racing the same two products in opposite order, released simultaneously via a
barrier), confirmed it **fails with a genuine Postgres deadlock error against the broken
code**, then confirmed it and the full 53-test inventory suite pass against real
Postgres with the fix restored.

## Prevention

- The regression test itself: any future change that reorders or removes the SKU sort
  will fail this test with a real deadlock, not a mocked assertion.
- **Not built in this fast-tracked pass:** an alert on Postgres deadlock errors, which
  the reveal's own guidance names as a metric that "should be exactly zero in normal
  operation" — currently nothing surfaces a deadlock except the 500 it causes. A counter
  incremented from the `psycopg.errors.DeadlockDetected` exception (mirroring how
  `UPSTREAM_CALLS` in orders-service already turns a timeout into a metric, not just a
  log line) would close this.
