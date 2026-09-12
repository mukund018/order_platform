# Incident index

Phase 3 log. One row per incident, filled in when the RCA is scored.

| ID | Title | Sev | Category | TTA (min) | TTM (min) | Hints | RCA score |
|---|---|---|---|---|---|---|---|
| INC-001 | Orders stall at PENDING and never come back | SEV2 | Configuration / environment | 1 | 5 | 0 | 9/10 |
| INC-002 | Restocked products keep selling zero | SEV2 | Caching | 2 | 10 | 0 | 9/10 |

## Rolling numbers

- Incidents closed: 2 / 12
- Median time to mitigate: 8 min
- Mean RCA score: 9.0 / 10

## Category coverage

Target: at least 9 of the 12 categories in CLAUDE.md section 8.2.

| # | Category | Covered by |
|---|---|---|
| 1 | Configuration / environment | INC-001 |
| 2 | Application logic bug | |
| 3 | Database performance | |
| 4 | Concurrency / race condition | |
| 5 | Resource exhaustion | |
| 6 | Dependency failure or slowness | |
| 7 | Data integrity across services | |
| 8 | Background job / queue behaviour | |
| 9 | Deployment / migration | |
| 10 | Time and timezone | |
| 11 | Caching | INC-002 |
| 12 | Retry / timeout / idempotency | |
