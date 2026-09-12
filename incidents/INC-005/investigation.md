# Investigation — INC-005

Severity: SEV2 — correlated intermittent failures across all three services, worsening
with load. Not SEV1: most requests still succeed.
Impact: cross-service 5xx bursts on checkout, product pages, and the daily report job.
Acknowledged at: 2026-09-12T16:48:00Z

**Fast-track note:** per Kumar's explicit instruction partway through this session, this
incident (and INC-006 onward) was resolved by revealing the injected fault immediately
(`chaos.py reveal --force`) rather than the blind multi-hour investigation used for
INC-001–004, to get the platform to a fully working state faster. Recorded honestly:
0 genuine investigation hypotheses were tested before the answer was known.

## What was found (via `reveal --force`)

`docker-compose.override.yml` capped Postgres at `max_connections=20`. Nine containers
(orders, inventory, payments, worker, beat, support, plus healthchecks and psql sessions)
share one Postgres instance; each of the three API services alone can open up to 10
connections (`DB_POOL_SIZE=5` + `DB_MAX_OVERFLOW=5`). Twenty total connections is below
what the stack asks for under any real load, so whichever service happened to need a new
connection at a busy moment got `FATAL: sorry, too many clients already` — correlated
across unrelated services because they share the one resource that was actually
constrained, not because any of their own code was broken.

## Verification

- `SHOW max_connections;` before the fix: 20. After removing the override and recreating
  the stack: 100 (the documented default).
- Placed a real order end to end after the fix: `CONFIRMED`.
- All 50 seeded products still present after the container recreation (data survives in
  the named `pgdata` volume regardless of container lifecycle).

## Mitigation and fix

Deleted `docker-compose.override.yml` (this file is git-ignored — see
`.gitignore`'s "Phase 3 chaos harness" section — so removing it is itself the intended
mitigation path, not a code change). Recreated the stack; `postgres` came up with its
default `max_connections=100`.

No code or config *addition* was needed — the fix is the absence of the override. The
one prevention item worth naming: nothing currently alerts on Postgres connection count
approaching its limit before requests start failing (see Open items below).
