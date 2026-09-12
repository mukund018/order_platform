To: Finance
Severity: SEV2 | Started: 2026-09-12T13:53:00Z | Ongoing: no — mitigated 14:05, reconciliation job live 14:10

Summary: A configuration mismatch between orders-service's payment timeout and
payments-service's simulated gateway latency caused roughly 1 in 3 payments to be
charged successfully by the gateway *after* orders-service had already given up and
recorded the order as FAILED. The customer was charged; our own records said they
weren't.

Impact: reconciling `orders_db` against `payments_db` directly (they are separate
databases with no cross-database join, so this required a script, not a query) found
**148 of 156** orders in the incident window marked FAILED that were, in fact, charged —
**₹15,32,637** held against customers who were told the order did not go through. A
one-off run of the new reconciliation job against the existing backlog after the fix
found a further 191 mismatches (query capped at 200 rows per run; there are more, and
subsequent scheduled runs will surface them).

Evidence: reconciliation is not a one-off check anymore — `payment_reconciliation_mismatch_total`
(Prometheus) now counts every mismatch as it's found, with a `PaymentReconciliationMismatch`
alert on any nonzero rate. Each mismatch is logged individually with `order_id`,
`amount_paise`, and `provider_ref`, so a per-order list is a log query away
(`tools/logtool.py errors` or a direct query on `payment_reconciliation_mismatch` events).

Ruled out: this is not ongoing — the configuration causing new mismatches was reverted
and the service that had the wrong setting was rebuilt with a guard that refuses to boot
if this specific misconfiguration recurs.

Ask: we do not have a mechanism to actually resolve a confirmed mismatch (refund, manual
fulfilment, or goodwill credit) — the reconciliation job finds and reports them, it
deliberately does not act on them, because the right action depends on facts it cannot
see (is the item still in stock? has the customer already complained and been refunded
some other way?). We need Finance's process for what happens to the ₹15,32,637+ already
identified, and whether refund vs. fulfilment should be the default going forward so we
can build that into the next version of this job.
