# Incident index

Phase 3 log. One row per incident, filled in when the RCA is scored.

| ID | Title | Sev | Category | TTA (min) | TTM (min) | Hints | RCA score |
|---|---|---|---|---|---|---|---|
| INC-001 | Orders stall at PENDING and never come back | SEV2 | Configuration / environment | 1 | 5 | 0 | 9/10 |
| INC-002 | Restocked products keep selling zero | SEV2 | Caching | 2 | 10 | 0 | 9/10 |
| INC-003 | Checkout gets slower every hour | SEV2 | Database performance | 1 | 15 | 3 | 9/10 |
| INC-004 | Customers charged for orders that failed | SEV2 | Dependency failure or slowness | 1 | 12 | 0 | 10/10 |
| INC-005 | Random failures across all three services at once | SEV2 | Resource exhaustion | 1 | 3 | 0 | 6/10 |
| INC-006 | The last unit of everything never sells | SEV3 | Application logic bug | 1 | 7 | 0 | 7/10 |
| INC-007 | The daily sales report does not match the dashboard | SEV3 | Time and timezone | 1 | 3 | 0 | 7/10 |
| INC-008 | Orders expire seconds after being placed | SEV1 | Background job / queue behaviour | 1 | 4 | 0 | 8/10 |
| INC-009 | Stock on hand does not match the warehouse | SEV3 | Data integrity across services | 1 | 7 | 0 | 8/10 |
| INC-010 | Customers getting the same confirmation email repeatedly | SEV3 | Deployment / migration | 1 | 3 | 0 | 9/10 |

## Rolling numbers

- Incidents closed: 10 / 12
- Median time to mitigate: 6 min
- Mean RCA score: 8.2 / 10

## Category coverage

Target: at least 9 of the 12 categories in CLAUDE.md section 8.2.

| # | Category | Covered by |
|---|---|---|
| 1 | Configuration / environment | INC-001 |
| 2 | Application logic bug | INC-006 |
| 3 | Database performance | INC-003 |
| 4 | Concurrency / race condition | |
| 5 | Resource exhaustion | INC-005 |
| 6 | Dependency failure or slowness | INC-004 |
| 7 | Data integrity across services | INC-009 |
| 8 | Background job / queue behaviour | INC-008 |
| 9 | Deployment / migration | INC-010 |
| 10 | Time and timezone | INC-007 |
| 11 | Caching | INC-002 |
| 12 | Retry / timeout / idempotency | |
