# Learning log

One entry per concept that was new or that I got wrong the first time. Concept, one-line
summary, and where it shows up in this repo. Written in my own words on purpose — if I
cannot summarise it in a line, I have not understood it yet.

Format:

```
## <date> — <concept>
**In one line:** …
**Where it appears here:** …
**What I got wrong first:** …
```

---

<!--
Still to fill in. Concepts worth an entry, roughly in the order they come up:

- virtualenvs and why the project has one venv but a pyproject.toml per service
- FastAPI dependency injection vs Express middleware
- SQLAlchemy 2.0 Session: when the transaction starts, and what commit/rollback/close do
- the lost update problem, and why UPDATE ... WHERE stock >= qty avoids it
- deadlocks, and why processing items in SKU order removes them
- Alembic revisions: why the schema is code and not something you change by hand
- pydantic v2 validation vs writing the checks by hand
- idempotency: the Idempotency-Key header, and the unique index that actually enforces it
- compensation: releasing a reservation when payment fails, and why it is not a rollback
- timeouts: why every outbound call has one, and what a timeout does NOT tell you
- ASGI middleware ordering, and why contextvars survive it but BaseHTTPMiddleware breaks it
- correlation ids and why print() debugging does not survive three services
- counters vs gauges vs histograms, and why you cannot average a percentile
- the RED method
- cache-aside, TTLs, and why a cache must never be able to take the service down
- celery: broker vs backend, prefork vs threads, and what makes a task safe to retry
- UTC in the database, IST at the business boundary, and half-open ranges
- reading a py-spy dump
-->
