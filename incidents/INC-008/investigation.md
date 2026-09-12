# Investigation — INC-008

Severity: SEV1 — orders are being destroyed mid-flight (an illegal state transition) and
stock accounting is being corrupted (stock released for a reservation the order flow may
then still commit). Not a full outage, but real customer-facing data corruption under
normal load.
Impact: orders flipping CONFIRMED-looking → EXPIRED seconds after being placed, roughly
1 in 20 checkouts erroring, worse under higher traffic.
Acknowledged at: 2026-09-12T14:56:00Z

**Fast-track note:** resolved via `chaos.py reveal --force` rather than a blind
investigation, per Kumar's instruction. 0 hypotheses tested before the answer was known.

## What was found (via `reveal --force`)

`ORDER_EXPIRY_MINUTES` was set to `0` in the live environment (`.env`), documented
default 15. The beat-scheduled `expire_stale_orders` job selects PENDING/RESERVED orders
older than `now - ORDER_EXPIRY_MINUTES`; at zero, that cutoff is simply "now," so any
order still being processed at the instant the job ticks (every 60s) matches. The job
releases its reservation and moves it to EXPIRED — then the request thread handling that
same order tries its own transition (to RESERVED or CONFIRMED), the state machine
correctly refuses the now-illegal transition, and that surfaces as the intermittent 500.
Worse under load because more concurrent in-flight orders means more chances to overlap
with a tick — a race, not a capacity problem, which is why "worse when busy" was the tell.

## Fix and verification

1. Reverted `.env`'s `ORDER_EXPIRY_MINUTES` to 15.
2. Added `MIN_ORDER_EXPIRY_MINUTES = 1` in `services/orders/app/config.py` and a `ge=`
   bound on the field — the service now refuses to boot with a value that could ever put
   a genuinely in-flight order inside the expiry window, the same principle as INC-001's
   `MIN_UPSTREAM_TIMEOUT_S`. Three new tests mirror the existing INC-001 config tests.
3. Verified live: rebuilt `orders`/`worker`/`beat` with the fix, placed a real order end
   to end, and it completed `CONFIRMED` with no race — previously this exact flow would
   have had a real chance of being expired out from underneath itself within the same
   minute.
