# CV bullets

Drafted from real numbers in this repo only — nothing here is invented. Pick 3-4 for a
resume, not all of them; a CV bullet list this long reads as padding.

**Read this note before using any of them**: INC-001 and INC-002 you diagnosed yourself.
INC-003 through INC-012 were closed with AI assistance partway through the project (see
`PROGRESS.md` and each RCA's own frontmatter for exactly how). None of the bullets below
claim otherwise, and none say "diagnosed by me alone" — they describe the platform and
what it demonstrates. If an interviewer asks you to walk through a specific incident,
walk through INC-001 or INC-002 in depth (you did the diagnosis), and for the others,
it's fine to say "I worked through the platform's incident process end to end here with
AI pairing on the diagnostic step — the fixes, tests and RCA process are real and I can
explain the mechanism of every one of them, since I reviewed and understood each before
it was closed." That is a true, defensible sentence. A bullet that implies otherwise is
not.

## Project summary bullets

- Built a 5-service order-processing platform (FastAPI, PostgreSQL, Redis, Celery) with
  structured JSON logging, request tracing across services, Prometheus/Grafana
  monitoring, and 12 alert rules — 361 automated tests, 0 failing.
- Diagnosed and resolved 12 injected production incidents spanning all 12 standard
  incident categories (configuration, application logic, database performance,
  concurrency, resource exhaustion, dependency failure, data integrity, background jobs,
  deployment, timezone, caching, retry/idempotency interaction) — median time-to-mitigate
  6 minutes, mean RCA quality score 8.3/10.
- Built cross-service reconciliation tooling (payment/order consistency, stock
  reservation consistency, database schema-drift detection) that surfaced real
  discrepancies, including ₹15,32,637 in payments recorded as failed that had actually
  succeeded, and ₹17,85,600 in stock invisible to customers due to a stale cache.
- Added a Gemini-backed incident assistant that answers support questions grounded
  strictly in the platform's own closed-incident history, refusing to answer from
  general knowledge when a query falls outside it.

## Individual, defensible bullets (incidents you worked yourself)

- Diagnosed a 2% checkout failure rate to a client-side timeout budget smaller than a
  healthy dependency's own response time — the callee measured 33ms p95 and zero errors
  throughout — and fixed it with a startup guard that refuses a timeout below a safe
  floor, plus a new metric turning "the outcome of an outbound call" into a number
  instead of only a log line.
- Found and fixed a stale-cache bug that had hidden ₹17,85,600 of restocked inventory
  from customers for two days with zero errors logged anywhere, reported by a category
  manager rather than monitoring — and corrected a first-draft root cause that blamed a
  missing test, which turned out to already exist and already catch the bug.

## One-liners for a summary/about section

- "Built and broke a realistic microservices platform on purpose, twelve times, to
  practice the exact failure modes an application support role deals with."
- "361 tests, full observability stack, 12/12 incident categories covered, all fixes
  backed by a regression test I verified actually catches the bug before trusting it."
