# INC-011 - Checkout failures during the flash sale

Reported by: Alert "HighErrorRate" (inventory) + marketing
Reported at: 19:40 UTC

Description:
We put SKU-0003 on the homepage at 19:30 and checkout started throwing errors
within a couple of minutes. Roughly 5% of attempts fail with a 500, and it is
not the same customers every time - retrying usually works.

The moment we pulled the promo the errors stopped. Put it back, they came back.

Inventory has plenty of stock, so it is not a genuine out-of-stock. Nothing is
slow - the failures are fast failures.

We want the promo back up tonight.
