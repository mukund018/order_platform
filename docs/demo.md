# 5-minute demo script

For a live interview or a recorded walkthrough. Replays INC-001 — a real closed incident,
picked because it has a single clean root cause and every step below is a command you can
actually run, not a slide.

Have the stack up before you start (`docker compose up -d`), Grafana open at
`http://localhost:3000`, and a terminal ready.

## 0. Set the scene (30 seconds)

"This is an order-processing platform I built to practice application support — twelve
production incidents were injected into it on purpose, and I diagnosed and fixed each one
using only the tools a real support engineer has: logs, metrics, and the database. I'm
going to break it live, right now, and show you the diagnosis."

## 1. Inject the fault (30 seconds)

INC-001's actual fault: `INVENTORY_TIMEOUT_S` cut from its documented `2.0` to `0.25`,
which is too small for a healthy dependency to reliably answer inside.

```powershell
# in .env, change:
INVENTORY_TIMEOUT_S=0.25
docker compose up -d --build orders
```

Say while it rebuilds: "Nothing about inventory changed — it's still completely healthy.
Watch what a too-small timeout on the *caller's* side does anyway."

## 2. Generate load and let the alert fire (60 seconds)

```powershell
python tools/traffic.py --rps 12 --duration 180
```

Switch to Grafana's request-health row. Point at `HighServerErrorRate` (or
`UpstreamTimeouts` if it's fired first) going from green to firing, and the orders p95
panel — inventory's own panel stays flat and fast the entire time. Say: "Inventory's own
latency panel never moves. That's the whole diagnostic problem — the thing that's
actually broken looks perfectly healthy."

## 3. Trace one failing request (60 seconds)

```powershell
python tools/logtool.py errors --since 5m
```

Pick one `request_id` from the output, then:

```powershell
python tools/logtool.py trace <request_id>
```

Point at the line: inventory answers **200** at some duration *after* orders already gave
up and logged the timeout. Say: "This is the whole root cause in one line — inventory
answered the request successfully, just after the caller had already stopped listening.
A timeout bounds what the client observes, not what the server measures."

## 4. Show the fix and why it can't regress (60 seconds)

```powershell
cat services/orders/app/config.py   # MIN_UPSTREAM_TIMEOUT_S and the `ge=` bound
```

Say: "The fix isn't picking a better number — any number is eventually wrong under
enough load. The fix is that this service now refuses to *start* with a timeout below a
floor that a healthy dependency can't be expected to beat. And `upstream_calls_total` — a
metric, not just a log line — is what let INC-004 and INC-011 get caught the same way
later: the outcome of every outbound call is now a number, not just prose in a log file."

## 5. Show the RCA (30 seconds)

```powershell
cat incidents/INC-001/rca.md
```

Scroll to the 5-Whys and the impact numbers. Say: "Every incident in this project has one
of these — root cause, quantified impact, what was ruled out and why, and what change
actually prevents it recurring, not just a mitigation."

## 6. Revert and close (30 seconds)

```powershell
# .env back to INVENTORY_TIMEOUT_S=2.0
docker compose up -d --build orders
```

Close with the numbers: "Twelve incidents, all twelve categories in a standard incident
taxonomy, median time-to-mitigate six minutes, and every fix is backed by a regression
test — several of which I verified by deliberately re-breaking the thing and confirming
the test actually catches it before trusting it."

---

**If asked to go deeper on any single incident**, INC-002 (the "no errors, and we're
losing money" one) and INC-011 (a deadlock verified to fail on the broken code and pass
on the fix) are the two with the most to say beyond this script.
