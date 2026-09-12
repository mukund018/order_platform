# Investigation — INC-012

Severity: SEV2 — periodic severe latency (5-6s spikes every few seconds) plus a genuine
1-2% error rate on the busiest endpoint, business-visible on both product pages and
checkout.
Impact: product pages "crawling" for a second or two on a repeating cycle, occasional
500s during the slow moments, checkout affected less severely.
Acknowledged at: 2026-09-12T15:34:00Z

**Fast-track note:** resolved via `chaos.py reveal --force` rather than a blind
investigation, per Kumar's instruction. 0 hypotheses tested before the answer was known.
This is the final incident of Phase 3.

## What was found (via `reveal --force`)

Two independently-reasonable "performance" changes, neither harmful alone, combined into
a cache stampede against a bottleneck:

1. `PRODUCT_CACHE_TTL` cut from 60s to 5s (`.env`).
2. `docker-compose.override.yml` capped inventory's `DB_POOL_SIZE` at 2 with
   `DB_MAX_OVERFLOW: 0` — "smaller pools measured better on the benchmark box."

All product cache keys are written at roughly the same moment (they're populated
together on a cache-aside miss), so with a 5-second TTL they also *expire* at roughly the
same moment. Every in-flight request then misses the cache simultaneously and queues for
one of only two database connections; requests that wait longer than the pool is willing
to give up raise, which is the 1-2% of 500s. The cache refills, the next several seconds
are fast, and the cycle repeats — the "heartbeat" in the latency panel is the TTL itself.
Neither Redis nor Postgres show any pressure in `docker stats`, because neither is
actually the bottleneck; the connection pool is.

## Fix and verification

**Reverted both changes**: `PRODUCT_CACHE_TTL` back to 60, the override deleted (its
whole purpose was carrying this and INC-005's fault — see `.gitignore`).

**Fixed the class of problem, not just the instance**, per the reveal's own guidance:
`services/inventory/app/cache.py` now jitters every write's TTL by ±20%
(`JITTER_FRACTION`), so keys populated together no longer expire together — a genuine
future TTL reduction can no longer reproduce this exact stampede shape. Added
`DatabasePoolSaturated` (`monitoring/alerts.yml`), firing when a service's pool has every
connection in use for 2 minutes — using metrics (`db_pool_connections`, `db_pool_size`)
that already existed but had no alert on them.

Two new tests (`services/inventory/tests/test_cache.py`): the existing TTL test updated
to allow the jittered range instead of an exact value, and a new test writing 20 keys and
asserting their TTLs are not all identical (and stay within ±20% of configured).

Verified live: rebuilt inventory with both configs reverted, confirmed the real running
container's environment (`PRODUCT_CACHE_TTL=60`, `DB_POOL_SIZE=5`), then ran a 40-second
load burst (20 rps, 70% browse mix) — **0 server errors, 100% success on non-business
requests, p50 16.7ms** — no heartbeat pattern.
