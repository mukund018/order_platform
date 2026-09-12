---
id: INC-012
title: a short cache TTL and a small connection pool, each safe alone, combined into a periodic cache stampede
severity: SEV2
services: [inventory]
category: Retry / timeout / idempotency interaction
detected_at: 2026-09-12T15:34:00Z
mitigated_at: 2026-09-12T15:40:00Z
resolved_at: 2026-09-12T15:44:00Z
time_to_acknowledge_min: 1
time_to_mitigate_min: 6
hints_used: 0
---

## Summary

`PRODUCT_CACHE_TTL=5` (documented default 60) and a `docker-compose.override.yml`
capping `DB_POOL_SIZE` at 2 with no overflow — both individually defensible
"performance work" changes, both landed the same week, neither one dangerous on its own.
Together: product cache keys are written in a batch (a cache-aside miss populates several
at once) and a short TTL means they also expire in a batch, so every in-flight request
misses the cache at the same instant and queues for one of two database connections.
Requests that outwait the pool raise — the 1-2% of 500s; the cache refills and the next
several seconds are fast, then it repeats. The "heartbeat" in the latency panel was
literally the TTL period. This is the incident that cannot be solved by finding one bad
line — it required noticing two separately-reasonable changes were only harmful
together, which is what a large share of real production incidents actually look like.

**Resolved via `chaos.py reveal --force`, not a blind investigation** — see
`investigation.md`. This closes Phase 3 — the twelfth and final injected incident.

## Impact

Periodic 5-6 second latency spikes on product pages recurring every few seconds, plus a
genuine 1-2% error rate during each spike, on the platform's busiest endpoint —
business-visible on both browsing and checkout.

## Resolution

Reverted both configuration changes. Then fixed the underlying class of problem rather
than only this instance, per the reveal's own recommendation:

1. **TTL jitter** (`services/inventory/app/cache.py`): every cache write's TTL now varies
   by ±20% (`JITTER_FRACTION`), so keys populated together no longer expire together.
   This is the standard defence against a cache stampede (the alternative, single-flight
   refresh, is a larger structural change than this incident's severity warranted).
2. **`DatabasePoolSaturated` alert** (`monitoring/alerts.yml`): fires when a service's
   pool has every connection in use for 2 minutes, using `db_pool_connections` /
   `db_pool_size` — metrics that already existed with no alert watching them.

Verified live against the real rebuilt container, not just the test suite: confirmed the
running environment's actual config, then ran a real load burst and got 0 server errors
and 100% success where the fault previously would have produced a repeating latency
pattern and real 500s.

## Prevention

- Jitter is the direct fix for *this* stampede shape; the pool alert is the earlier
  warning for the *next* one, whatever combination of changes causes it.
- Two new tests assert the jittered TTL falls in the expected range and that keys
  populated together do not all get the identical expiry.
- **Not built in this fast-tracked pass:** nothing currently reviews a "performance"
  config change against the *combination* of settings it interacts with — the review
  gap the reveal names outright ("reviewing changes one at a time is exactly how this
  gets shipped"). A capacity-review checklist for any change to a pool size, cache TTL,
  or timeout — reviewed together, not each in isolation — is a process fix, not a code
  one, and belongs in whatever this project's next deploy process looks like.

## Lessons learned — for all of Phase 3

This is the last of the twelve. Read together, the twelve incidents cluster around a
small number of real lessons, not twelve unrelated bugs:
- **The code was almost never wrong.** INC-003, INC-005, INC-006 (well, this one was),
  INC-009, INC-010 and INC-012 were all correct code undone by drift, config, or a
  runtime state nothing was checking — only INC-006 and INC-011 were genuine application
  bugs. Reading the diff is not always where the answer is.
- **A metric that doesn't exist yet is the recurring gap.** `upstream_calls_total`
  (INC-001), `payment_reconciliation_mismatch_total` (INC-004), schema-drift checking
  (INC-003/INC-010), and now pool saturation (INC-012) were each built *because* an
  incident proved nothing was watching that specific number — not before.
- **"It only happens under load" and "it's periodic" are both specific claims, not
  vague ones** — the first points at concurrency or a shared resource (INC-005, INC-011),
  the second points at a timer (INC-012). Naming which one you're looking at narrows the
  search before any code gets read.
