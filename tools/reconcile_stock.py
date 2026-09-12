"""INC-009: find stock stranded by ACTIVE reservations whose order has already reached
a terminal state - an order flow is done with a reservation once it is CONFIRMED,
FAILED, EXPIRED, or CANCELLED, but nothing releases the stock unless something asks.

Cross-database, on purpose: inventory owns stock_reservations and has no idea whether
the order on the other end is still alive, and orders owns that answer. Neither
database's own foreign keys can express this invariant, so it has to be checked here
instead - see docs/decisions.md.

Run from the host against the published ports:

    python tools/reconcile_stock.py                 report only
    python tools/reconcile_stock.py --release        also release the stranded stock
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from typing import Any

import httpx

DEFAULT_INVENTORY_URL = "http://localhost:8002"
DEFAULT_ORDERS_URL = "http://localhost:8001"
DEFAULT_TIMEOUT = 10.0

TERMINAL_ORDER_STATUSES = {"CONFIRMED", "FAILED", "EXPIRED", "CANCELLED"}


def find_stranded(inventory_url: str, orders_url: str, timeout: float) -> list[dict[str, Any]]:
    with httpx.Client(timeout=timeout) as client:
        active = client.get(f"{inventory_url}/reservations/active").raise_for_status().json()

        stranded = []
        for row in active:
            order = client.get(f"{orders_url}/orders/{row['order_id']}")
            if order.status_code == 404:
                # The order row itself is gone; the reservation still is not.
                stranded.append({**row, "order_status": "MISSING"})
                continue
            order.raise_for_status()
            status = order.json()["status"]
            if status in TERMINAL_ORDER_STATUSES:
                stranded.append({**row, "order_status": status})
        return stranded


def release(inventory_url: str, order_id: str, timeout: float) -> None:
    with httpx.Client(timeout=timeout) as client:
        client.post(f"{inventory_url}/reservations/{order_id}/release").raise_for_status()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--inventory-url", default=DEFAULT_INVENTORY_URL)
    parser.add_argument("--orders-url", default=DEFAULT_ORDERS_URL)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument(
        "--release", action="store_true", help="release the stranded reservations found"
    )
    args = parser.parse_args()

    stranded = find_stranded(args.inventory_url, args.orders_url, args.timeout)

    if not stranded:
        print("no stranded reservations found")
        return 0

    by_sku: dict[str, int] = defaultdict(int)
    for row in stranded:
        by_sku[row["sku"]] += row["qty"]

    print(f"{len(stranded)} stranded reservation(s) across {len(by_sku)} sku(s):\n")
    for sku, qty in sorted(by_sku.items()):
        print(f"  {sku}: {qty} units stranded")

    if args.release:
        print("\nreleasing...")
        # A MISSING order (the row does not exist in orders_db at all) is still
        # released: inventory's release endpoint only touches its own
        # stock_reservations rows and has no reason to require the order to exist.
        released_orders = {row["order_id"] for row in stranded}
        for order_id in released_orders:
            release(args.inventory_url, order_id, args.timeout)
        print(f"released {len(released_orders)} order(s)'s reservations")
    else:
        print("\nrerun with --release to return this stock")

    return 0


if __name__ == "__main__":
    sys.exit(main())
