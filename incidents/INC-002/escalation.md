To: Meera (category management), cc: retail ops, finance
Severity: SEV2 | Started: unknown, at least since the Monday restock | Ongoing: yes at time of writing

*This is a status update to the business, not a technical escalation. The fault is in
inventory-service, which we own, so there is no other team to hand it to. Meera raised it
and is losing money on it, so she gets told what is happening in her own terms — and
finance is copied because there is a number.*

---

## What is happening, in one line

The website is showing "sold out" for products we actually have in stock. The stock itself
is fine — it is in the warehouse, it is in the database, and the admin screens are right.
Only the customer-facing product pages are wrong.

## Why your restocked lines sold zero

When you restock a line, the new number is saved correctly. But the website keeps showing
customers a **saved copy** of the old number for up to thirty minutes, and every time a
customer looks at the page that copy gets refreshed for another thirty minutes.

The copy is only corrected when somebody **buys** that product. And nobody buys a product
that says sold out. So a restocked line can stay invisible indefinitely — it is stuck in a
loop that only a sale would break, and the sale cannot happen.

That is also why it hit four of your eleven lines rather than all of them. The other seven
happened to get an order through at the right moment.

## What it is costing

Measured on the live system a few minutes ago:

| | |
|---|---|
| Product lines showing sold out while stock exists | **12** |
| Units invisible to customers | **720** |
| Retail value of that stock | **₹17,85,600** |

Those are units we could sell today and cannot. The true cost is higher than the ticket
suggested: you noticed four lines, and there are twelve.

To size the lost revenue properly I need one number from you: the normal daily sales rate
for the affected lines. Multiply that by the days since Monday and we have the figure for
finance. I do not want to guess it.

## What I have confirmed

- Your stock is real. I placed a test order for one of the "sold out" lines and it went
  through and confirmed normally. Nothing is wrong with the goods or the data.
- No orders have been lost or mispriced. Nothing needs correcting after the fact.
- Nothing was erroring, which is why no alarm went off and why nobody noticed until you
  did. That is a gap on our side, not yours.

## What happens next

1. **Now:** I am reverting the two configuration changes that caused this. Product pages
   should show correct stock within a minute of that going out.
2. **Then:** I will re-run the comparison and confirm all 12 lines read correctly, rather
   than assume it.
3. **This week:** an automated check so the website and the database are compared
   continuously, and this can never again be something a category manager has to notice.

## What I need from you

- The normal daily units for the affected lines, for the revenue figure.
- A heads-up when the next bulk restock happens, so I can watch it go through cleanly.
- Please keep raising these. "These lines sold literally zero" was a better signal than
  anything our monitoring produced, and it is the only reason this was found at all.
