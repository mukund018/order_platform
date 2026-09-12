# Runbook — a container will not start after a migration

**Symptom:** a service container exits or crash-loops immediately, and the last log lines
come from alembic rather than from uvicorn.

The entrypoint runs `alembic upgrade head` before starting the application. That is
deliberate — a service must never serve traffic against a schema it does not expect — but
it means a migration failure presents as "the service is down".

## Read the actual error

```powershell
docker compose logs --tail 60 <service>
```

Alembic's message is usually precise. The ones that come up:

| Message | Cause |
|---|---|
| `Can't locate revision identified by '<hash>'` | The database is at a revision this image does not contain. Almost always a rollback: old image, newer database. |
| `Target database is not up to date` | Two heads, or a manual change to `alembic_version`. |
| `relation "x" already exists` | The migration is being applied to a database that already has the table — often because someone ran `create_all` at some point. |
| `DuplicateTable` / `UndefinedColumn` | The migration and the live schema disagree about what exists. |
| Connection refused | Not a migration problem at all — PostgreSQL is not up. Check `docker compose ps postgres`. |

## Find out where the database actually is

```powershell
docker compose exec postgres psql -U app -d orders_db -c "SELECT * FROM alembic_version;"
docker compose run --rm --entrypoint sh orders -c "alembic history --verbose | head -40"
```

Comparing the stored revision against the image's history tells you which direction the
mismatch runs.

The `--entrypoint sh` matters: without it the container runs the migration again on the
way in, and you get the same failure instead of a prompt.

## Two heads

```powershell
docker compose run --rm --entrypoint sh orders -c "alembic heads"
```

More than one line means two migrations were written against the same parent — the usual
result of two branches merging. Fix it properly with a merge revision rather than
deleting one:

```powershell
docker compose run --rm --entrypoint sh orders -c "alembic merge -m 'merge heads' <head1> <head2>"
```

## Mitigation

**If the last deploy caused it**, roll the image back to the previous tag. Note that this
only works if the migration was not applied, or was backwards compatible. A migration that
dropped a column cannot be undone by starting an older image.

**If the database is ahead of the image**, the fastest safe route is forward: deploy the
newer image rather than trying to downgrade the schema.

**Never** edit `alembic_version` by hand to make the error go away. It stops the error and
leaves the schema in a state nothing can reason about afterwards.

## In development only

If this is a local stack and the data is disposable, starting clean is faster than
unpicking it:

```powershell
docker compose down -v
docker compose up -d --build
.venv\Scripts\python.exe tools\seed.py
```

`down -v` deletes the `pgdata` volume and everything in it. Make sure that is what you
want before running it.

## Prevention

Migrations should be written so that the old and the new code can both run against the
intermediate schema: add a nullable column, deploy the code that writes it, backfill, then
make it `NOT NULL` in a later migration. Doing all three in one step is what makes
rollbacks impossible.
