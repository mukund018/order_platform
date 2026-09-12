---
id: INC-001
title: orders-service abandoned healthy inventory calls after a timeout was cut to 250ms
severity: SEV2
services: [orders, inventory]
category: Configuration / environment
detected_at: 2026-09-12T08:47:41Z
mitigated_at: 2026-09-12T08:52:09Z
resolved_at: 2026-09-12T09:04:51Z
time_to_acknowledge_min: 1
time_to_mitigate_min: 5
hints_used: 0
---

## Summary

`INVENTORY_TIMEOUT_S` was `0.25` in the running environment instead of the documented
`2.0`. Every call orders-service makes to inventory-service had a 250ms budget. Inventory
was entirely healthy — p95 of 33ms, zero 5xx — but the budget is measured by the *client*
and includes time the request spends queued before the server begins handling it. The
slowest couple of percent of calls exceeded it, so orders-service hung up on requests that
inventory then went on to answer successfully.

## Impact

Measured over 08:47:41–08:50:42 UTC (3 minutes at 12 rps):

| | |
|---|---|
| `POST /orders` attempts | 1051 |
| Failed with 504 | 22 (**2.1%**) |
| `upstream_timeout` events | 23 (9 on `/reservations`, 14 on pricing reads) |
| Orders left stuck in `PENDING` | 9 |
| Stock held by reservations that cannot complete | 38 units across 18 rows |

Customer-visible effect: checkout spun and then errored, and the order appeared as
"Pending" and stayed there. Because a retry usually succeeded, the number of customers who
*noticed* is higher than the number of failed requests — the ticket mentions one customer
with three Pending orders for the same item.

No money was lost or taken incorrectly: a reservation timeout never reaches the payment
step. The cost is abandoned checkouts and stock made temporarily unsellable.

## Timeline (UTC)

| Time | Event |
|---|---|
| 08:47:41 | Load begins; first `upstream_timeout` within seconds |
| 08:48 | Acknowledged. SEV2 assigned. Order-status counts pulled — 9 PENDING is the anomaly |
| 08:49 | `logtool errors --since 10m` surfaces 23 × `UPSTREAM_TIMEOUT` with `timeout_s=0.25` |
| 08:50 | inventory-service ruled out: zero 5xx, p95 33ms across 5188 requests |
| 08:50 | `logtool trace` on one failing request shows inventory answering it 200 in 159ms, 28ms after orders had given up |
| 08:51 | `INVENTORY_TIMEOUT_S=0.25` confirmed in `.env` against `2.0` in `.env.example` |
| 08:52:09 | **Mitigated** — value reverted to 2.0, orders container recreated |
| 08:54:15 | **Verified** — identical load for 120s: 0 server errors, 0 `upstream_timeout` |
| 09:03:09 | Beat job expires the first stuck order (`orders_expired count=1`) |
| 09:04:51 | **Resolved** — all 9 orders EXPIRED, ACTIVE reservations back to 0, all 38 units returned |

Time to acknowledge: 1 minute. Time to mitigate: 5 minutes.

## Detection — how it was noticed, and could it have been sooner?

It was reported by a customer-support colleague, and the `PendingOrdersStuck` alert fired
at about the same time. The alert did its job.

It could have been noticed sooner and more specifically. Nothing watches the rate of
`upstream_timeout` events, which is the signal that points directly at the cause instead of
at a downstream consequence. `PendingOrdersStuck` says "something is wrong with checkout";
an alert on timeouts per upstream would have said "orders-service is giving up on
inventory", which is most of the investigation.

The other gap is that nothing compares the running configuration against the documented
defaults. A single `grep` answered this incident, but only after twenty minutes of
narrowing down where to grep.

## Root cause — 5 Whys

1. **Why did customers see checkout fail?** orders-service returned 504 on ~2% of attempts.
2. **Why 504?** Its calls to inventory-service exceeded the configured timeout and were
   abandoned as `UPSTREAM_TIMEOUT`.
3. **Why were they exceeded, when inventory's p95 is 33ms?** Because the timeout was 250ms
   and it bounds *client-observed* latency — connection acquisition plus queueing plus
   service time — not the server's own handling time. On the traced request, inventory spent
   159ms handling a call the client had already been waiting 252ms for.
4. **Why was the timeout 250ms?** `INVENTORY_TIMEOUT_S` was set to `0.25` in the deployed
   environment. The repository's documented value is `2.0`.
5. **Why did that reach production unnoticed?** Nothing validates timeout values against the
   latency the dependency actually delivers, nothing alerts on timeout rate per upstream, and
   nothing compares the running environment with `.env.example`. The configuration is trusted
   completely and checked nowhere.

**Root cause:** a timeout budget set below the client-observed latency of a healthy
dependency, in an environment where no guardrail exists to catch a timeout value that the
dependency cannot meet.

## Resolution

**Mitigation (08:52).** `INVENTORY_TIMEOUT_S` back to `2.0`, orders container recreated.
Verified by re-running the identical load profile: 1440 requests, **0 server errors, 0
`upstream_timeout` events**, p99 424ms.

**Stuck orders.** The 9 PENDING orders were left to the existing expiry job rather than
touched by hand. That job is idempotent, it releases the reservation before moving the
order to EXPIRED, and it refuses to mark an order terminal if the release fails — three
properties a manual `UPDATE` would not have. Nothing needed fixing for them; they needed
fifteen minutes.

Confirmed rather than assumed. The job logged `orders_expired count=1` at 09:03:09 and
`count=8` at 09:04:07, and by 09:04:51:

```
orders_db     : 867 CONFIRMED, 165 FAILED, 9 EXPIRED   (0 PENDING, 0 RESERVED)
inventory_db  : 0 ACTIVE reservations, 0 units held    (all 38 returned)
```

Worth saying plainly: for ten minutes I could not tell "the expiry job is broken" apart
from "the expiry job has not reached them yet", and the difference was fifteen minutes of
waiting. Concluding at T+6 that the job was broken would have been wrong, and would have
led to exactly the manual `UPDATE` the job exists to avoid.

**Permanent fix.** The value itself was never the real defect — the absence of anything
that would catch it was. See Prevention.

## Prevention

1. **Regression test (added).** `services/orders/tests/test_config.py` asserts that each
   outbound timeout is at least `MIN_UPSTREAM_TIMEOUT_S`, and `config.py` now enforces that
   floor through pydantic, so a container configured with a timeout too small to be
   survivable refuses to start instead of discovering it under customer load. A value that
   can only produce failures is a configuration error, and configuration errors belong at
   startup. Verified against a real container: booting orders with `INVENTORY_TIMEOUT_S=0.25`
   now exits with `INVENTORY_TIMEOUT_S: Input should be greater than or equal to 0.5`
   rather than serving traffic.
2. **Metric and alert (added).** The outcome of every outbound call is now a counter,
   `upstream_calls_total{upstream, outcome}`, incremented in `clients/base.py` — this
   incident was logged perfectly and measured nowhere, which is why every dashboard stayed
   green through it. The `UpstreamTimeouts` rule in `monitoring/alerts.yml` fires on a
   non-zero timeout rate for two minutes and points at `runbooks/upstream-timeouts.md`.
   That alert names the cause instead of a consequence and would have cut the
   investigation to one panel.
3. **Dashboard panel (to add).** Client-observed upstream latency next to the configured
   timeout for that upstream, on the same axes. The gap between "how fast is the dependency"
   and "how long am I willing to wait" should be visible, not inferred.
4. **Config drift check (to add).** Compare the running environment against `.env.example`
   at startup and log any key whose value differs from the documented default. Not an error
   — plenty of values legitimately differ — but a line in the log that this incident would
   have been solved by.

## Lessons learned

- **A timeout is a statement about the caller's patience, not about the callee's speed.**
  Both services were healthy by their own measurements while every call between them failed.
  Asking "is inventory slow?" produced a confident "no" and nearly sent the investigation
  to the wrong team.
- **The client and the server measure different things, and the difference is the point.**
  Inventory's 159ms and orders' 252ms for the same request are both correct. Roughly 93ms
  of queueing lives between them, invisible to either service alone. A timeout budget has to
  be set against the client's number.
- **Two services disagreeing about whether a call succeeded is a fingerprint.** It means the
  caller stopped listening. That single observation, available from one `logtool trace`,
  identified the class of problem before anything else was ruled out.
- **"Nobody deployed" rules out code, not configuration.** It is a useful fact and an
  incomplete one, and treating it as complete would have wasted the whole investigation.
- **Waiting is a valid action, and knowing how long to wait is part of the job.** The
  difference between "the recovery job is broken" and "the recovery job has not got there
  yet" was fifteen minutes and one log line. Reaching for a manual fix at minute six would
  have bypassed the safety the job exists to provide.
- **Waiting is a valid action, and knowing how long to wait is part of the job.** The
  difference between "the recovery job is broken" and "the recovery job has not got there
  yet" was fifteen minutes and one log line.
- **The PENDING-on-reservation-timeout design worked exactly as intended**, and is worth
  defending: a timeout is ambiguous, so failing the order could release stock that was
  genuinely reserved. Leaving it for the idempotent expiry job is the safe branch. It also
  made the symptom look stranger than a plain error, which is the price of that safety.
