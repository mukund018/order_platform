# Runbook — the live database disagrees with the models

**Symptom:** something that should be impossible according to the code is happening
anyway — a query behaves as if an index isn't there (see
[slow-query-missing-index.md](slow-query-missing-index.md)), or a task that's supposed to
be idempotent isn't (duplicate rows, duplicate side effects like emails), or a `NOT NULL`
/ default that the model declares doesn't seem to be enforced. Nothing in the code is
wrong, `git diff` shows nothing on the incident branch, and the test suite is green.

## Why this happens

The model and the checked-in Alembic migration describe what the schema *should* be.
Neither one is the schema — the live database is, and nothing keeps the two in sync
automatically after the migration has run once. A manual `ALTER TABLE` / `DROP INDEX` /
`DROP CONSTRAINT` run directly against a live database (a hotfix, an ad hoc maintenance
script, a restore from an older backup) silently drifts the database away from what the
models and migrations still say — and stays that way until something notices.

## Confirm it

```powershell
python tools/check_schema_drift.py
```

Wraps `alembic check` (Alembic's own comparison between the live schema and the
SQLAlchemy models — the same diffing `alembic revision --autogenerate` uses) across
orders, inventory and payments. A clean service prints "No new upgrade operations
detected"; a drifted one names exactly what's missing, e.g.:

```
FAILED: New upgrade operations detected: [('add_constraint', UniqueConstraint(...))]
```

To check one service by hand:

```powershell
docker compose exec orders alembic check
```

## Mitigation

Recreate the missing schema object directly (the fastest path back to a correct
database):

```powershell
docker compose exec postgres psql -U app -d orders_db -c "ALTER TABLE <table> ADD CONSTRAINT <name> UNIQUE (<columns>);"
```

Then re-run `check_schema_drift.py` to confirm it agrees.

## What this does not do

It does not tell you *why* the drift happened, or *when* — Alembic only compares the
current state, it keeps no history of the divergence. That is a question for whoever has
access to run ad hoc statements against production (see `incidents/INC-003/escalation.md`
and `INC-010`'s for the actual asks made on this).

## Prevention

This check is currently a manual, on-demand command. It is not yet:
- run automatically after a deploy,
- run on a schedule,
- wired to an alert.

Two incidents (INC-003, a missing index; INC-010, a missing unique constraint) were both
this same root cause. Building the automation is the natural next step — the check itself
already exists and is proven to work.
