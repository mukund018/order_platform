# Investigation — INC-007

Severity: SEV3 — no customer impact, but finance is closing the books on the wrong
number. Books close Friday, so this had a real deadline.
Impact: the automated daily report has understated sales for at least 3 consecutive
days; finance was about to trust the wrong figure.
Acknowledged at: 2026-09-12T14:49:00Z

**Fast-track note:** resolved via `chaos.py reveal --force` rather than a blind
investigation, per Kumar's instruction. 0 hypotheses tested before the answer was known.

## What was found (via `reveal --force`)

`previous_business_day()` (`services/orders/app/service.py`) computed
`datetime.now(UTC).date() - timedelta(days=1)` instead of computing "today" in the
business timezone first. `business_day_bounds()` — used by the reports *API* — already
did this correctly. Beat fires the report job at 00:05 IST, which is 18:35 UTC the
*previous* UTC day; subtracting a day from the UTC date lands one day further back than
intended. The job never errors — it just reports a different, wrong day, every day.

Notably: `services/orders/tests/test_reports.py` already had a test covering exactly
this boundary (`test_the_previous_business_day_is_the_local_one`, frozen at 19:30 UTC /
01:00 IST) and it was failing before this fix — the regression coverage already existed,
it just hadn't been run since the fault was introduced.

## Fix and verification

`datetime.now(UTC).date()` → `datetime.now(ZoneInfo(timezone)).date()` in
`previous_business_day()`. The existing freeze-time test now passes.

Verified live per the ticket's own reproduction steps: ran
`daily_sales_report` by hand against the live worker (`celery ... call
app.tasks.daily_sales_report`) — it picked `2026-09-11`, correctly "yesterday" in IST —
and compared against `GET /reports/daily?date=2026-09-11`: both agree on date, window,
and figures.
