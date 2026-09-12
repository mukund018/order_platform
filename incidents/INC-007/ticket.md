# INC-007 - Yesterday's sales report is wrong

Reported by: Finance (Anand)
Reported at: 06:10 UTC

Description:
The automated sales report that lands every morning does not match what I get
when I pull the same day from the reports API myself. The automated one is
lower, and it is not a rounding thing - it is a completely different set of
orders.

I checked three days back and all three are wrong in the same direction. The
numbers I pull by hand agree with the Grafana orders panel, so I think the
manual ones are right and the emailed one is wrong.

We close the books on Friday, so I need to know which number to trust.
