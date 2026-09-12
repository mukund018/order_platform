---
id: INC-007
title: scheduled daily report computed "yesterday" in UTC instead of the business timezone
severity: SEV3
services: [orders, worker]
category: Time and timezone
detected_at: 2026-09-12T14:49:00Z
mitigated_at: 2026-09-12T14:52:00Z
resolved_at: 2026-09-12T14:53:00Z
time_to_acknowledge_min: 1
time_to_mitigate_min: 3
hints_used: 0
---

## Summary

`previous_business_day()` used `datetime.now(UTC).date()` instead of computing the
current date in the business timezone (`Asia/Kolkata`) first. `business_day_bounds()` —
which the reports API uses — was already correct, so the API and the scheduled job
silently disagreed on which day they meant. Beat fires at 00:05 IST (18:35 UTC the day
before), which is exactly the 5.5-hour window each day where the UTC date and the IST
date differ — any job scheduled inside it is exposed. The job never errored; it reported
a real, internally consistent, entirely wrong day.

**Resolved via `chaos.py reveal --force`, not a blind investigation** — see
`investigation.md`. Worth noting: a regression test for exactly this boundary already
existed (`test_the_previous_business_day_is_the_local_one`) and had been failing — the
coverage was there, it simply hadn't been checked since the fault landed.

## Impact

Three consecutive days of understated automated sales reports reaching finance, ahead of
a Friday books-close deadline. No customer-facing effect — this is a reporting-accuracy
incident, not an availability one.

## Resolution

`datetime.now(UTC).date()` → `datetime.now(ZoneInfo(timezone)).date()` in
`previous_business_day()` (`services/orders/app/service.py`). Verified live: ran the
scheduled task by hand against the real worker container and compared its output to the
reports API for the same date — both now agree on date, window, and figures.

## Prevention

- The existing freeze-time test is the regression guard and is now passing again — no
  new test needed, but its prior failing state going unnoticed is itself worth naming:
  **nothing in this project's CI-equivalent (the pre-push hook) runs on a schedule**, only
  on push. A test can fail the moment a fault lands and stay red indefinitely if nobody
  pushes past it or opens the suite. This is the same underlying gap `docs/decisions.md`
  already names under "CI."
- The deeper structural fix, not built in this fast-tracked pass: one shared "what
  business day is it" function that both `business_day_bounds()` and
  `previous_business_day()` call, so the two code paths cannot drift apart again by
  construction rather than by test coverage alone.
