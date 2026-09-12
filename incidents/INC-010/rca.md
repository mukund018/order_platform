---
id: INC-010
title: dropped unique constraint turned a retry-safe confirmation task into a duplicate-email machine
severity: SEV3
services: [orders, worker]
category: Deployment / migration
detected_at: 2026-09-12T15:16:00Z
mitigated_at: 2026-09-12T15:19:00Z
resolved_at: 2026-09-12T15:25:00Z
time_to_acknowledge_min: 1
time_to_mitigate_min: 3
hints_used: 0
---

## Summary

`uq_notifications_order_id_kind` was missing from the live `orders_db` — present and
correct in both the SQLAlchemy model and the checked-in Alembic migration, dropped
directly from the running database, outside any deploy or migration. `send_confirmation`
achieves idempotency by inserting and catching the constraint violation, which is the
correct, race-free design — but it makes the constraint the mechanism, not an
optimisation. Every legitimate Celery retry (the task is `acks_late`, retries on
transient database errors, and a worker restart mid-task is exactly this) inserted
another notification row and "sent" another email. The task reported success every time,
so nothing in the logs pointed at a failure — because there wasn't one.

**Resolved via `chaos.py reveal --force`, not a blind investigation** — see
`investigation.md`.

## Impact

Customers receiving 2-4 duplicate confirmation emails for the same order over a few
minutes. No financial or stock impact — orders were correct and charged once; this is a
reputational/trust incident, not a money one.

## Resolution

Recreated `uq_notifications_order_id_kind` directly on the live database. No existing
duplicate notification rows were found to deduplicate (checked before and after).

**The prevention gap INC-003 left open is now closed, for both incidents.**
`tools/check_schema_drift.py` wraps `alembic check` (Alembic's own autogenerate-diff
comparison between the live schema and the models) across all three services that own
one. Verified round-trip: deliberately dropped the constraint again, ran the tool — it
reported `orders: DRIFTED` and named the exact missing constraint in Alembic's own
output — then restored it and confirmed clean.

## Prevention

- `tools/check_schema_drift.py`, verified to actually detect drift (not just assumed to
  work) — this single tool is the prevention item for both this incident and INC-003's
  "no schema drift check" open item.
- **Not built in this fast-tracked pass:** the tool is on-demand, not scheduled or
  wired into an alert. The natural next step, same shape as the last two reconciliation
  tools built this session (`reconcile_payment_mismatches`, `reconcile_stock.py`): a
  periodic job (or at minimum a documented step in a deploy runbook) that runs it
  automatically rather than relying on someone remembering to.
