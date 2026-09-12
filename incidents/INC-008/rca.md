---
id: INC-008
title: ORDER_EXPIRY_MINUTES=0 raced the expiry job against in-flight orders
severity: SEV1
services: [orders, worker]
category: Background job / queue behaviour
detected_at: 2026-09-12T14:56:00Z
mitigated_at: 2026-09-12T15:00:00Z
resolved_at: 2026-09-12T15:02:00Z
time_to_acknowledge_min: 1
time_to_mitigate_min: 4
hints_used: 0
---

## Summary

`ORDER_EXPIRY_MINUTES=0` (documented default: 15) made the expiry job's cutoff exactly
"now" — so any PENDING/RESERVED order still being processed at the moment the beat tick
fired (every 60s) was indistinguishable from a genuinely abandoned one. The job released
its stock reservation and marked it EXPIRED while the original request was still in the
middle of transitioning it to RESERVED or CONFIRMED; the state machine correctly refused
the now-illegal transition, surfacing as an intermittent 500. This is a background job
and a live request racing on the same row because the job's selection window was wrong,
not because the job or the state machine had a bug.

**Resolved via `chaos.py reveal --force`, not a blind investigation** — see
`investigation.md`.

## Impact

SEV1: real orders destroyed mid-flight (visible to a customer as a confirmed-looking
order flipping to Expired seconds later) and real stock-accounting corruption (stock
released for a reservation the request flow could still go on to commit). Roughly 1 in 20
checkouts erroring, worsening with concurrency — worse when busy because busier means
more overlap between in-flight requests and the once-a-minute job, not more load in the
capacity sense.

## Resolution

1. `.env`'s `ORDER_EXPIRY_MINUTES` reverted to 15.
2. `services/orders/app/config.py`: added `MIN_ORDER_EXPIRY_MINUTES = 1` and a `ge=` bound
   on the field. A value that could ever place a genuinely in-flight order (bounded by
   `INVENTORY_TIMEOUT_S + PAYMENTS_TIMEOUT_S`, a few seconds, and by the 60s beat tick
   itself) inside the expiry window is a configuration error, and the service now refuses
   to boot with one — the same principle as INC-001's `MIN_UPSTREAM_TIMEOUT_S`, applied
   to the timing assumption this job depends on.
3. Verified live: rebuilt `orders`/`worker`/`beat`, placed a real order end to end,
   confirmed successfully with no race.

## Prevention

- Three new tests (`services/orders/tests/test_config.py`) mirror the existing INC-001
  coverage: the floor is enforced, the floor value itself is accepted, and the documented
  default stays above it.
- **Not built in this fast-tracked pass:** a test asserting the expiry *query itself*
  never selects an order younger than the configured window, independent of the config
  guard — the guard prevents the bad value from being configured, but a query-level test
  would also catch a future change to the cutoff calculation itself (e.g., an off-by-one
  in the `timedelta` arithmetic) that a config bound alone would not.
