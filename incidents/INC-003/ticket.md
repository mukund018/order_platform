# INC-003 - Checkout is getting slower through the day

Reported by: Alert "HighLatencyP95" + Rahul (ops)
Reported at: 13:05 UTC

Description:
The p95 latency alert for orders-service fired at 13:02. What is odd is the
shape: at 09:00 this morning p95 was about 180ms, at 11:00 it was about 400ms,
and it is now over a second and still climbing. Nothing was deployed.

Orders are still completing - we are not losing sales yet - it just keeps
getting slower and slower. Restarting the orders container did not help. CPU on
the containers looks normal.

It behaves like it will keep degrading until something falls over.
