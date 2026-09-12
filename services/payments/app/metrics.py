from prometheus_client import Counter, Histogram

PAYMENT_ATTEMPTS = Counter(
    "payment_attempts_total",
    "Charge attempts sent to the card gateway, by outcome",
    ["result"],
)

# Buckets stretch well past a second: when the gateway goes slow that is exactly the
# range we need resolution in, and the default buckets stop at 10s.
PAYMENT_GATEWAY_DURATION = Histogram(
    "payment_gateway_duration_seconds",
    "Time spent inside a single gateway charge call",
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 10.0, 30.0),
)
