---
id: INC-002
title: Restocked products kept reading as sold out because the stock write stopped invalidating the cache
severity: SEV2
services: [inventory]
category: Caching
detected_at: 2026-09-12T11:40:00Z
mitigated_at: 2026-09-12T09:15:50Z
resolved_at: 2026-09-12T09:16:12Z
time_to_acknowledge_min: 2
time_to_mitigate_min: 10
hints_used: 0
---

> Note on the clock: the ticket carries the reporter's stated time (11:40) and the
> engineering timestamps are the platform's own UTC. They do not line up, and that is
> normal — a reporter's "this morning" is not a log timestamp. Everything measured below
> is platform UTC.

## Summary

Two changes shipped together as a Redis-load optimisation: `PRODUCT_CACHE_TTL` went from 60
to 1800 seconds, and the `cache.invalidate(sku)` call was removed from
`PATCH /products/{sku}/stock`. With nothing invalidating on a stock write, the TTL became
the only thing that could ever refresh a product — so a restock stayed invisible to
customers for up to thirty minutes at a time, and because browsing re-populates the key the
moment it expires, in practice the stale value was almost always the one being served.

Nothing errored. No alert fired. The database, the warehouse and the admin screens were all
correct. Only the customer-facing read was wrong, and it was wrong confidently and quickly.

## Impact

Measured on the running system:

| | |
|---|---|
| Product lines showing sold out while stock existed | **12 of 50** |
| Units invisible to customers | **720** |
| Retail value of that stock | **₹17,85,600** |
| Errors logged across all services during the window | **0** |
| Orders lost or mispriced | none — every order that *was* placed was correct |

The loss is entirely opportunity cost: stock we held, could have sold, and hid. Converting
it to a revenue figure needs the normal daily sales rate for those lines, which is a
question for category management and is deliberately not estimated here.

A detail from the ticket worth keeping: the reporter noticed **four** lines. There were
twelve. The person closest to the business caught a third of an incident that our
monitoring caught none of.

## Timeline

| Time | Event |
|---|---|
| — | Two changes ship together in a "reduce Redis load" commit |
| 11:40 | Category manager reports that restocked lines are selling zero |
| 11:42 | Acknowledged. SEV2: no outage, ongoing revenue loss |
| 11:42 | `logtool errors` confirms the reporter — **zero** errors or warnings. Every log-based approach is out |
| 11:43 | Reproduced: PATCH returns `stock: 60`, `GET` returns `stock: 0`, repeatedly |
| 11:44 | `psql` shows the row is correct at 60 — the write landed, the read is lying |
| 11:45 | `redis-cli GET` shows a cached copy 14 minutes older than the row; `TTL` says 1777s left |
| 11:46 | `PRODUCT_CACHE_TTL` is 1800 in `.env`, 60 in `.env.example` |
| 11:46 | `api/products.py`: `adjust_stock` commits and returns with no `cache.invalidate(sku)` |
| 11:48 | Test order for a "sold out" line reaches CONFIRMED — the data is genuinely fine |
| 11:50 | Blast radius measured: 12 SKUs, 720 units, ₹17,85,600 |
| 09:15:50 | **Mitigated** — both changes reverted, inventory rebuilt |
| 09:16:12 | **Resolved** — a fresh restock is visible on the very next read; 0 of 50 SKUs disagree |

## Detection — how it was noticed, and could it have been sooner?

It was noticed by a category manager looking at sales figures, two days after it started.
Nothing in the platform noticed at all, and nothing in the platform *could* have: every
signal we collect is about rate, errors and duration, and this incident changed none of
them. The service was fast, correct by its own lights, and wrong.

This is the monitoring gap the incident exposed, and it is a class of gap rather than a
missing panel. RED metrics answer "is the system working". They cannot answer "is the system
telling the truth". Correctness needs signals of its own, and we had none.

The cheapest one would have caught it in minutes: compare what the API reports against what
the database holds, for a handful of SKUs, on a schedule. That check is now the first
prevention item.

## Root cause — 5 Whys

1. **Why were restocked lines selling zero?** The catalogue showed them as sold out, so
   customers never tried to buy them.
2. **Why did the catalogue show sold out when the database said 60?** Reads are cache-aside
   through Redis, and the cached copy still held the pre-restock value.
3. **Why was the cached copy not replaced when the stock changed?** The
   `cache.invalidate(sku)` call had been removed from the stock-adjustment endpoint, so a
   write no longer touched the cache at all.
4. **Why did the TTL not cover for that?** It had been raised from 60 to 1800 seconds in the
   same change. A minute of staleness is a rounding error; thirty minutes is a business day's
   worth of a customer's attention. And because any browse repopulates the key for another
   full TTL, the stale value is served almost continuously.
5. **Why did neither change get caught?** This is the uncomfortable one. A test that catches
   the invalidation half **already existed** — `test_stock_adjustment_invalidates_both_keys`
   warms the cache, adjusts stock and asserts the next read shows the new value. I confirmed
   it by re-applying the fault and running the suite: it fails, exactly as it should. So the
   change did not slip past a gap in the tests. **It reached a running environment without
   the test suite being run at all** — there is no CI pipeline, and `ruff check . && pytest`
   is a gate somebody has to remember. The TTL half had no test and would have passed either
   way.

**Root cause:** the write path stopped invalidating the cache, and a TTL raised thirty-fold
turned what would have been a one-minute inconsistency into a permanent one. The deeper
cause is that a change reached a running environment without running a test suite that
would have rejected it, in a system with no signal for "serving a stale answer".

## Why it could not recover on its own

The reservation endpoints **do** invalidate correctly (`api/reservations.py` calls
`_invalidate` on reserve and release). So the cache is repaired whenever somebody buys the
product.

That is what made this self-sustaining rather than merely stale. A restocked line reads as
sold out → nobody buys it → nothing invalidates the key → it reads as sold out. The only
event that would repair the cache was the event the stale cache prevented.

It also explains why four of eleven lines were affected rather than all eleven: the others
happened to receive an order at the right moment and healed themselves. The apparent
inconsistency in the ticket was the signature of the bug, not noise in the report.

## Resolution

**Mitigation (09:15:50).** Both changes reverted — `PRODUCT_CACHE_TTL` back to 60, the
`cache.invalidate(sku)` call restored — and inventory-service rebuilt.

**Verified (09:16:12), not assumed.** With a stale key deliberately in place
(`SKU-0011`: catalogue 0, database 60), a `PATCH +25` produced:

```
database 85, catalogue 85, list 85   - visible on the very next read
SKUs where catalogue and database disagree: 0 of 50
```

**One thing the revert did not do.** Immediately after the rebuild, 12 SKUs still
disagreed. Restoring the code stops *new* staleness; it does not purge keys already sitting
in Redis with their old TTL. Those clear when the key next expires or is invalidated. If
this ever recurs at a worse moment, the mitigation is not complete until the affected keys
are dropped:

```bash
docker compose exec redis redis-cli DEL inventory:products:all
# and per sku: redis-cli DEL inventory:product:SKU-0008
```

This is now in the runbook, because "I reverted it and it is still wrong" is exactly the
moment an engineer starts doubting a correct diagnosis.

## Prevention

1. **A gate that runs without being remembered (the real fix).** The regression test that
   catches this already exists and already fails against the fault — I verified that rather
   than assumed it. Adding another test would fix nothing. What is missing is anything that
   *runs* the suite before a change reaches an environment. `docs/decisions.md` lists "no CI"
   under **Later — noted, not built**; this incident is the argument for moving it. Until
   then the honest mitigation is a pre-push hook running `ruff check . && python run_tests.py`,
   which is minutes of work and would have stopped this outright.
2. **TTL bounded in config (added).** `PRODUCT_CACHE_TTL` had a lower bound (`ge=1`) and no
   upper one, so the 1800 was accepted silently — this is the half of the change no test
   covered. It now carries `le=MAX_PRODUCT_CACHE_TTL` (300s), with a regression test, so a
   thirty-minute staleness window cannot be configured by accident. A cache TTL is a
   statement about how wrong the system is allowed to be, and that deserves a ceiling.
3. **Consistency check (to add).** A scheduled comparison of the API's answer against the
   database for the tracked SKUs, exported as a gauge and alerted on when non-zero. This is
   the signal class we did not have, and it is what would have caught this in minutes rather
   than two days.
4. **Review rule (to add).** A change that alters caching behaviour is a correctness change,
   not a performance change, and needs to answer one question in review: *how stale can this
   get, and who notices?*

## Lessons learned

- **RED metrics cannot see this class of failure.** Rate, errors and duration all say the
  system is healthy when it is quickly and confidently serving the wrong answer. Correctness
  needs its own signals, and "no errors" is not evidence that anything is right.
- **A business report can be better evidence than a dashboard.** "These lines sold literally
  zero" was the only signal that existed. Zero is not a slow week — it is a different
  mechanism — and taking that word literally is what started the investigation in the right
  place.
- **Two systems disagreeing about one fact points at a cached copy, not a broken write.**
  The admin screen and the website disagreeing was in the first paragraph of the ticket; it
  named the layer before any tool was opened.
- **Check whether your diagnostic is also a write.** My proof-of-life order invalidated the
  key for the SKU I was investigating, and the next measurement came back clean and
  contradicted a reproduction I had done two minutes earlier. I nearly talked myself out of a
  correct diagnosis with evidence I had destroyed myself.
- **Two safe changes can be unsafe together.** Removing an invalidation is survivable with a
  60-second TTL. Raising a TTL is survivable with invalidation intact. Reviewing changes one
  at a time is how the combination ships.
- **Check whether the test you are about to write already exists.** My first draft of this
  RCA blamed a missing regression test and proposed adding one. The test was already there
  and already caught the fault; I only found that out by re-applying the fault and running
  the suite. Had I not checked, the incident would have closed with a fix that changed
  nothing and a root cause that was wrong — and the actual hole, a change reaching an
  environment with no gate in front of it, would still be open.
- **Reverting code does not revert state.** Cached data, queued messages and database rows
  written under the fault all outlive the deploy that fixes it. Mitigation is not finished
  until the state is checked too.
