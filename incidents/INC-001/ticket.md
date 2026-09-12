# INC-001 - Checkout is timing out for a lot of customers

Reported by: Customer support (Priya, retail ops)
Reported at: 09:14 UTC

Description:
Since about 09:00 we have had a steady stream of customers telling us that
placing an order "spins and then says something went wrong". Some of them try
again and it works, some of them try four times and give up.

What is strange is that when I look the orders up in the admin list, a lot of
them ARE there - they just sit on "Pending" and stay there. One customer has
three Pending orders for the same thing because she kept retrying.

Examples:
- "order failed" reported at 09:07, 09:09, 09:12 (three separate customers)
- the PagerDuty alert "PendingOrdersStuck" went off at 09:11
- the site itself loads fine, product pages are quick

Nobody deployed anything this morning as far as I know.
