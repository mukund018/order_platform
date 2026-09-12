# INC-009 - Warehouse count does not match the system

Reported by: Warehouse (Suresh) via ops
Reported at: 08:55 UTC

Description:
We did a spot count on eight lines this morning. On five of them the physical
count is higher than what the system says we have available - by between 4 and
30 units. On the other three we match exactly.

The affected lines are selling, so it is not that they are frozen. It is just
that the system thinks less is available than we actually have, so we are
refusing orders we could fulfil.

Nothing is erroring. The stock numbers move normally when we sell things - they
are just wrong by a constant amount.
