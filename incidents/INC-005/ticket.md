# INC-005 - Everything is flaky at once

Reported by: Alert "HighErrorRate" (orders) then (inventory) then (payments)
Reported at: 16:48 UTC

Description:
Three separate 5xx alerts inside four minutes, one for each service. Support is
seeing failures on checkout, on product pages, and the daily report job errored
too.

It is not a clean outage - most requests work, then a burst fails, then it
recovers. It gets noticeably worse when traffic is higher.

All nine containers are up and healthy in docker ps. Postgres is up. Redis is
up. We cannot find one thing that is broken, which is the problem.
