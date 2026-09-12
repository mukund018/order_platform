# The catalogue disagrees with the database

**No alert covers this yet** — that is the point of the incident that produced this
runbook. It arrives as a business report, not as a page.
**Typical severity:** SEV2. No outage, no errors, and revenue leaving continuously.
**First seen:** INC-002

## What it sounds like when it arrives

It will not come from monitoring. It comes from a person:

- "we restocked on Monday and those lines have sold *zero*"
- "the site says sold out but the admin says 60"
- "the price on the website is last month's"
- "nothing is erroring, it just looks wrong"

Take the disagreement literally. Two parts of one system reporting different values for the
same fact, with no errors anywhere, almost always means something is serving a **stale**
copy rather than computing a **wrong** answer. That distinction picks your tools: you are
looking for a cache, not a bug in a calculation.

"Zero sales" is worth special attention. Zero is not a slow week — it is a different
mechanism.

## Establish it in three questions

Ask the same question through three doors. They should agree, and one of them will not.

```bash
SKU=SKU-0008

# 1. What does the customer see?
curl -s localhost:8002/products/$SKU | python -m json.tool

# 2. What does the database hold?
docker compose exec -T postgres psql -U app -d inventory_db \
  -c "SELECT sku, stock, price_paise, updated_at FROM products WHERE sku='$SKU';"

# 3. What is cached, and for how long?
docker compose exec redis redis-cli GET "inventory:product:$SKU"
docker compose exec redis redis-cli TTL "inventory:product:$SKU"
docker compose exec redis redis-cli TTL "inventory:products:all"
```

| Result | Meaning |
|---|---|
| API disagrees with the database, cached value matches the API | **Stale cache.** Continue below. |
| API and database agree | The reporter is looking at something else — a different environment, a browser cache, a CDN |
| Database itself is wrong | Not a caching incident. This is data integrity — see `runbooks/`, and check for stranded reservations |

Compare the TTL with what the design says it should be:

```bash
grep PRODUCT_CACHE_TTL .env .env.example
docker compose config | grep -i cache_ttl      # what is actually running
```

## Size it before you fix it

The person who reported it will have seen a fraction of it, and the number is what gets
this prioritised. Compare every SKU, not just theirs:

```bash
python - <<'PY'
import subprocess, httpx
api = {p["sku"]: p["stock"] for p in httpx.get("http://localhost:8002/products", timeout=15).json()}
rows = subprocess.run(
    ["docker","compose","exec","-T","postgres","psql","-U","app","-d","inventory_db",
     "-t","-A","-F",",","-c","SELECT sku, stock FROM products ORDER BY sku;"],
    capture_output=True, text=True).stdout.strip().splitlines()
db = {r.split(",")[0]: int(r.split(",")[1]) for r in rows if "," in r}
wrong = {s: (api[s], db[s]) for s in db if s in api and api[s] != db[s]}
hidden = {s: v for s, v in wrong.items() if v[0] == 0 and v[1] > 0}
print(f"{len(wrong)} of {len(db)} SKUs disagree; {len(hidden)} shown sold out with stock")
print(f"units invisible to customers: {sum(r for _, r in hidden.values())}")
PY
```

> **Do not place a test order for an affected SKU before you have measured.** Reserving
> stock invalidates that SKU's cache key, so buying one repairs the very thing you are
> trying to measure. In INC-002 this produced a measurement of "0 SKUs affected" two
> minutes after a clean reproduction, and nearly overturned a correct diagnosis. Measure
> first, prove second.

## Find which write forgot the cache

Reads are cache-aside. Every path that changes a product must invalidate it. Check each:

```bash
grep -rn "cache.invalidate" services/inventory/app/api/
```

Expected: `create_product`, `adjust_stock` (both in `api/products.py`), and reserve and
release (via `_invalidate` in `api/reservations.py`). A missing one is your cause.

**Why a missing invalidation can be self-sustaining rather than temporary.** Buying a
product invalidates its key. So if the *stock write* forgets to invalidate, a restocked line
reads as sold out → nobody buys it → nothing invalidates it → it reads as sold out. The only
event that would repair the cache is the one the stale cache prevents. Expect it to persist
far beyond one TTL, and expect it to hit only *some* lines — the ones that happened to get
an order through healed themselves.

## Mitigate — in two parts

**1. Fix the cause.** Restore the invalidation, or the TTL, or both. Rebuild:

```bash
docker compose up -d --build inventory
```

**2. Purge what is already stale.** This is the step that gets forgotten, and skipping it
makes a correct fix look like a failed one — immediately after the rebuild in INC-002, 12
SKUs still disagreed. Restoring code stops *new* staleness; it does not touch keys already
sitting in Redis with their old TTL.

```bash
docker compose exec redis redis-cli DEL inventory:products:all
docker compose exec redis redis-cli DEL "inventory:product:SKU-0008"
```

To clear the lot — acceptable here because the cache is an optimisation and a cold cache
costs one database read per product:

```bash
docker compose exec redis redis-cli --scan --pattern 'inventory:product*' \
  | xargs -r docker compose exec -T redis redis-cli DEL
```

Never `FLUSHALL`: Redis db1 is the Celery broker, and you would drop queued tasks.

## Verify, do not assume

Prove a write is now visible on the next read, starting from a warm cache:

```bash
SKU=SKU-0011
curl -s localhost:8002/products/$SKU >/dev/null                      # warm it
docker compose exec -T postgres psql -U app -d inventory_db -t -A \
  -c "SELECT stock FROM products WHERE sku='$SKU';"                  # the truth
curl -s -X PATCH localhost:8002/products/$SKU/stock \
  -H 'content-type: application/json' -d '{"delta": 25}' >/dev/null
curl -s localhost:8002/products/$SKU | python -m json.tool           # must be truth + 25
```

Then re-run the blast-radius script above and confirm it reports 0 disagreements.

## Prevention checklist

- [ ] Does every write path to `products` invalidate the cache?
- [ ] Is `PRODUCT_CACHE_TTL` inside its bounds? (`ge=1`, `le=300` — a value outside them
      now stops the service booting)
- [ ] Did the change that caused this run `python run_tests.py`? `.githooks/pre-push` runs
      it automatically; enable it with `git config core.hooksPath .githooks`
- [ ] Is there a scheduled check comparing the API's answer against the database? **Still
      outstanding** — it is the signal that would turn this from a business report into an
      alert
