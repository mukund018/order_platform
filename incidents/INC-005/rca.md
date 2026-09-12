---
id: INC-005
title: docker-compose.override.yml capped Postgres at max_connections=20, starving all three services under load
severity: SEV2
services: [orders, inventory, payments]
category: Resource exhaustion
detected_at: 2026-09-12T16:48:00Z
mitigated_at: 2026-09-12T20:35:00Z
resolved_at: 2026-09-12T20:36:00Z
time_to_acknowledge_min: 1
time_to_mitigate_min: 3
hints_used: 0
---

## Summary

`docker-compose.override.yml` (git-ignored, part of the Phase 3 chaos harness) set
Postgres's `max_connections` to 20. Nine containers share that one Postgres instance;
the three API services alone can claim up to 30 connections between them at full pool
size (10 each), before the worker, beat, and healthchecks are counted. Once the shared
ceiling was below what the stack could ask for, whichever service happened to need a
connection at a busy moment got `FATAL: sorry, too many clients already` — correlated
across services because the constrained resource was shared, not because any service's
own code was wrong. This is why "everything is flaky at once, but nothing looks broken
in isolation" is the signature of a shared-resource limit rather than a bug.

**Resolved via `chaos.py reveal --force`, not a blind investigation** — see
`investigation.md` for the reasoning (Kumar's explicit instruction, prioritizing a fully
working platform over the deeper diagnostic exercise for the remaining incidents).

## Impact

Correlated 5xx bursts across orders, inventory, and payments, worsening under load; the
daily report job also errored (same shared Postgres, same limit). No data loss —
connections were refused, not corrupted.

## Resolution

Deleted `docker-compose.override.yml` (removing it *is* the fix — it is git-ignored and
exists only to carry this and other environment-only faults) and recreated the stack.
`max_connections` returned to Postgres's default (100). Verified: a real order placed end
to end after the fix confirmed successfully, and all 50 seeded products were intact.

## Prevention

- **Still open:** nothing currently alerts on Postgres's own connection count approaching
  `max_connections` — only on each service's *own* SQLAlchemy pool usage
  (`db_pool_connections`, see `runbooks/db-pool-exhausted.md`), which would not have
  caught a ceiling that's too low for the sum of all pools combined. A `postgres_exporter`
  target in Prometheus, a panel for connections-used-vs-limit, and an alert before the
  limit is reached (not after) would close this gap — not built in this fast-tracked pass.
- Since the fault lived entirely in an environment file with no code or schema
  involvement, no regression test applies here the way one did for INC-003/004 — the
  right test would be an integration check that the stack's *configured* pool sizes stay
  under `max_connections` with headroom, which belongs with the prevention item above.
