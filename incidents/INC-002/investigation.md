# Investigation — INC-002

Severity: **SEV2**. No outage, no errors, and money is leaving the business every hour it
continues. Severity is about impact, not about how loud the failure is — and this one is
silent by construction.

Impact: restocked products keep telling customers they are sold out. Measured live: **12
SKUs, 720 units, ₹17,85,600 of retail stock invisible to customers** while the database,
the warehouse and the admin screens all agree it is there.

Acknowledged at: 11:42 UTC (reported 11:40).

## Triage notes

The reporter is not technical and her ticket is better evidence than most. Three things in
it are worth taking literally:

1. *"the stock report in the admin says 60"* — one part of our own system already
   disagrees with another part. That is the whole shape of the incident, handed over in the
   first paragraph.
2. *"Nothing is erroring... the site is fast"* — so there is nothing to grep for and
   nothing on the RED dashboards will help. Every tool that answers "what is broken" is
   going to say "nothing".
3. *"four of them have sold literally zero"* — zero is not a slow week. Zero is a
   different mechanism from slow.

Two systems disagreeing about the same fact, with no errors, means something is serving a
**stale** answer rather than a wrong one. The question is not "what is broken" but "where
is the answer coming from".

## Log

| Time | Hypothesis | Tool / action | Finding | Result |
|---|---|---|---|---|
| 11:42 | Is anything erroring at all? | `logtool errors --since 3m`, ops console | **"no errors or warnings in the window"** — confirms the reporter | confirmed, and rules out every log-based approach |
| 11:43 | Is the reporter mistaken, or is it reproducible? | Pick a sold-out SKU, browse it, `PATCH /products/SKU-0008/stock {delta: 60}` | PATCH returns `200` with `stock: 60` | write path works |
| 11:43 | Does the read path agree? | `GET /products/SKU-0008` immediately, then at +5s and +10s | `stock: 0` every time. Also `stock: 0` in `GET /products` | **reproduced** |
| 11:44 | Which one is lying — the write or the read? | `psql inventory_db`, `SELECT stock FROM products WHERE sku='SKU-0008'` | **60**, `updated_at 09:07:42` | the write landed; the read is wrong |
| 11:44 | So where does the read get its answer? | `docs/architecture.md` — reads are cache-aside through Redis | there is a cache between them | **lead** |
| 11:45 | What is in the cache? | `redis-cli GET inventory:product:SKU-0008` | `{"stock": 0, "updated_at": "08:53:31"}` — fourteen minutes older than the row | **confirmed stale** |
| 11:45 | How long will it stay wrong? | `redis-cli TTL inventory:product:SKU-0008` | **1777** seconds. Nearly thirty minutes | the value is not about to fix itself |
| 11:46 | Is that TTL intended? | `grep PRODUCT_CACHE_TTL .env .env.example` | `.env` = **1800**. `.env.example`, the documented default = **60** | drift |
| 11:46 | A long TTL alone should not matter if writes invalidate. Do they? | Read `services/inventory/app/api/products.py` | `adjust_stock` commits and returns. **There is no `cache.invalidate(sku)` call** | **root cause** |
| 11:48 | Is the stock genuinely sellable, or is the database wrong too? | Place a real order for 3 × SKU-0008 | **CONFIRMED**, no failure reason | data is fine; only the read path lies |
| 11:50 | How many lines are affected, and what is it worth? | Restock every sold-out SKU, then compare the API against the database | **12 of 50 SKUs wrong, 720 units, ₹17,85,600 hidden** | impact quantified |

## The evidence, in one place

Three answers to the same question at the same moment:

```
PATCH /products/SKU-0008/stock  {"delta": 60}   ->  200  {"stock": 60}
psql  SELECT stock FROM products WHERE sku='SKU-0008'  ->  60   (updated_at 09:07:42)
GET   /products/SKU-0008                        ->  200  {"stock": 0}

redis-cli GET inventory:product:SKU-0008
  {"sku":"SKU-0008", "stock":0, "updated_at":"2026-09-12T08:53:31Z"}
redis-cli TTL inventory:product:SKU-0008
  1777
```

The cached copy is fourteen minutes older than the row it is meant to represent, and Redis
intends to keep serving it for another twenty-nine minutes.

Configuration drift, found by comparing the running value with the documented one:

```
.env          PRODUCT_CACHE_TTL=1800
.env.example  PRODUCT_CACHE_TTL=60
```

And the missing line, in `api/products.py`:

```python
product = service.adjust_stock(session, sku, payload.delta)
session.commit()
return ProductOut.model_validate(product)      # <- cache.invalidate(sku) belongs here
```

## The part that makes it self-sustaining

The reservation endpoints **do** invalidate correctly — `api/reservations.py` calls
`_invalidate(cache, rows)` on both reserve and release. So the cache is refreshed whenever
somebody *buys* the product.

That is what turns a stale value into a trap. A restocked line reads as sold out, so nobody
buys it, so nothing ever invalidates the key, so it reads as sold out. The only event that
would repair the cache is the event the stale cache prevents. Left alone, the TTL eventually
expires and the line reappears — thirty minutes later, and then only until the next restock.

It also explains the ticket precisely. Meera said *four* of eleven restocked lines sold
zero. Not all eleven — because the ones that happened to get an order in before the restock,
or whose key had expired, were fine. The inconsistency in her report is the signature, not
noise in it.

## A mistake worth recording

My first attempt at measuring the blast radius came back **"0 of 50 SKUs disagree"** —
flatly contradicting the reproduction I had done two minutes earlier.

The cause was my own proof-of-life order. Buying 3 × SKU-0008 invalidated that key, so by
the time I measured, the one SKU I had been working on was the one SKU that was correct.
I had destroyed the evidence with the probe.

The fix was to restock a batch of lines and *not* buy any of them. The lesson is general:
during an incident, check whether your diagnostic action is also a write. Here it was, and
it repaired exactly the thing I was trying to measure.

## Ruled out

- **Anything erroring.** Zero warnings or errors across all services for the whole window.
- **The database.** `products.stock` is correct and `updated_at` moves on every PATCH.
- **The order flow.** An order for a "sold out" line goes to CONFIRMED. Reservation reads
  stock straight from the database, so it never sees the stale value.
- **inventory-service being down or slow.** `/ready` green, p95 unchanged, no latency
  complaint in the ticket or on the dashboard.
- **A genuine stock problem.** The warehouse, the database and the admin view all agree.
  Only the customer-facing read disagrees.

## One more check, after the diagnosis

Before writing the RCA I re-applied the fault and ran the inventory suite, to find out
whether a test should have caught it.

`test_stock_adjustment_invalidates_both_keys` **fails against the fault** — it warms the
cache, adjusts stock and asserts the next read shows the new value. So the test already
exists and already works, and the change could not have passed `python run_tests.py`.

That moves the question from "which test is missing" to "how did this reach a running
environment without the suite being run", which is a different and more useful answer. The
TTL half had no test and would have passed regardless.

## Conclusion

Two changes, together: `PRODUCT_CACHE_TTL` was raised from 60 to 1800, and the
`cache.invalidate(sku)` call was dropped from the stock-adjustment endpoint. With no
invalidation, the TTL is the only thing that ever refreshes a product — so a restock stays
invisible to customers for up to thirty minutes at a time, and because browsing re-populates
the key the moment it expires, in practice the stale value is almost always the one being
served.
