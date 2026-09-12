# Investigation — INC-010

Severity: SEV3 — customer-visible and reputationally bad (duplicate emails), but no
money or stock at risk; orders themselves were correct and charged once.
Impact: customers receiving 2-4 copies of the same confirmation email over a few
minutes; started after a maintenance window.
Acknowledged at: 2026-09-12T15:16:00Z

**Fast-track note:** resolved via `chaos.py reveal --force` rather than a blind
investigation, per Kumar's instruction. 0 hypotheses tested before the answer was known.

## What was found (via `reveal --force`)

`uq_notifications_order_id_kind` was missing from the live `orders_db` — confirmed via
`\d notifications`. `send_confirmation` is idempotent by design via insert-and-catch
(`IntegrityError` → treat as already-sent), which is correct and race-free, but it means
the unique constraint is not an optimisation, it *is* the idempotency mechanism. With it
gone, every legitimate Celery retry (a database blip, `acks_late` plus a worker restart —
nothing exotic) inserted another notification row and "sent" another email, and the task
still reported success — nothing in the logs to find, because nothing failed.

This is the same underlying gap as INC-003 (model and migration correct, live database
silently drifted from both), just on a different table and a different kind of schema
object (a unique constraint here, an index there).

## Fix and verification

Recreated the constraint directly (`ALTER TABLE notifications ADD CONSTRAINT
uq_notifications_order_id_kind UNIQUE (order_id, kind)`) — no duplicate rows existed yet
to deduplicate first (checked: `SELECT order_id, count(*) ... HAVING count(*) > 1`
returned zero rows).

**Built the actual prevention this time, not just a note for later** — INC-003's RCA left
"a schema-drift check against alembic head" as an open item, and this incident is the
same gap recurring. `alembic check` already does exactly this comparison (the same
autogenerate diffing behind `alembic revision --autogenerate`); `tools/check_schema_drift.py`
wraps it for all three services that own a schema. Verified round-trip against the live
stack: dropped the constraint again on purpose, ran the tool — it correctly reported
`orders: DRIFTED` and named the exact missing constraint — then restored it and confirmed
clean.
