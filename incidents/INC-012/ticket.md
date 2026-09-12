# INC-012 - The site goes slow every few seconds

Reported by: Alert "HighLatencyP95" (inventory) + several internal reports
Reported at: 17:12 UTC

Description:
Product pages are mostly instant, then for a second or two everything crawls -
five, six seconds to load - then it is fine again. It repeats. One of the devs
described it as "a heartbeat".

We are also seeing occasional 500s on product pages during the slow moments,
maybe 1-2%, which we have never had before.

Checkout is affected too but less badly. Redis and Postgres both look healthy in
docker stats - nothing is pegged.

Two changes went out in last week's performance work, both of which were
supposed to make things faster.
