# Runbook — a service is down

**Alert:** `ServiceDown` · **Severity:** SEV1 if orders, SEV2 otherwise

Prometheus has not been able to scrape one of `orders`, `inventory`, `payments` or
`worker` for over a minute. Either the process is dead, it is wedged, or the network
between Prometheus and it is broken.

If the service is **orders**, no customer can place an order. Treat it as SEV1 and
acknowledge immediately.

## First five minutes

```powershell
docker compose ps
docker compose logs --tail 100 <service>
```

`docker compose ps` tells you which of three very different situations you are in:

| What you see | What it means |
|---|---|
| Container is missing or `exited` | It crashed or was never started. Read the logs from the bottom. |
| Container is `restarting` | It is crash-looping — usually a config or migration failure at startup. |
| Container is `running` but unhealthy | The process is alive but not serving. Wedged, or a dependency is gone. |

## Crashed or crash-looping

The last few log lines before the exit carry the reason. The usual ones here:

- **A missing or malformed environment variable.** pydantic-settings fails loudly at
  import time with the field name. Compare `.env` against `.env.example`.
- **`alembic upgrade head` failed.** The entrypoint runs migrations before the app, so a
  bad migration stops the container before it ever listens. See
  [failed-migration.md](failed-migration.md).
- **Cannot reach PostgreSQL.** Check `docker compose ps postgres` — if the database
  itself is down, this alert is a symptom and not the problem.

## Running but not serving

```powershell
curl.exe -s -o NUL -w "%{http_code}" http://localhost:8001/health
curl.exe -s http://localhost:8001/ready
```

- `/health` answers, `/ready` returns 503 → the process is fine, a dependency is not.
  The `checks` object in the response body names which one.
- Neither answers → the process is wedged. Every worker thread is probably blocked on
  something. Find out on what before you restart, or you will lose the evidence:

```powershell
docker compose exec <service> pip install py-spy
docker compose exec <service> py-spy dump --pid 1
```

A stack full of `psycopg` waits means the database or the connection pool. A stack full
of `httpx` waits means a downstream service is not answering.

## Mitigation

```powershell
docker compose restart <service>
```

A restart clears a wedged process and is the right first move once you have a `py-spy`
dump. It does **not** fix a crash loop — that needs the underlying config or migration
problem solved first.

Note the time you restarted. That is your time-to-mitigate.

## Escalation

Escalate when the service comes back and immediately wedges again, or when the cause sits
in a component another team owns. Bring: the `py-spy` dump, the last 100 log lines, the
time it started, and how many orders were affected.

## Afterwards

Check for work that was interrupted mid-flight:

```powershell
docker compose exec postgres psql -U app -d orders_db -c "SELECT status, count(*) FROM orders WHERE created_at > now() - interval '1 hour' GROUP BY status;"
```

Orders left `PENDING` or `RESERVED` will be cleaned up by `expire_stale_orders` within
`ORDER_EXPIRY_MINUTES`. If the count is not falling, the worker is not running either.
