"""Fill inventory-service with a catalogue so the other tools have something to work on.

Run from the host against the published port, not from inside a container:

    python tools/seed.py --count 50 --seed 7
"""

from __future__ import annotations

import argparse
import random
import sys
from typing import Any

import httpx

DEFAULT_BASE_URL = "http://localhost:8002"
DEFAULT_COUNT = 50
DEFAULT_TIMEOUT = 5.0

# Every twelfth sku starts sold out so the out-of-stock path is reachable without
# having to drain a product first.
SOLD_OUT_EVERY = 12
MAX_STOCK = 200
MIN_PRICE_RUPEES = 99
MAX_PRICE_RUPEES = 4999

MATERIALS = (
    "Stainless Steel",
    "Bamboo",
    "Ceramic",
    "Cotton",
    "Brushed Aluminium",
    "Walnut",
    "Recycled Paper",
    "Silicone",
    "Copper",
    "Canvas",
)

ITEMS = (
    "Kettle",
    "Travel Mug",
    "Desk Lamp",
    "Notebook",
    "Water Bottle",
    "Cable Organiser",
    "Chopping Board",
    "Storage Box",
    "Pen Stand",
    "Laptop Sleeve",
    "Wall Clock",
    "Spice Jar Set",
    "Tote Bag",
    "Phone Stand",
    "Serving Tray",
)

VARIANTS = ("", "", " Mini", " Pro", " Large", " Classic", " 2-Pack")


def build_product(index: int, rng: random.Random) -> dict[str, Any]:
    sku = f"SKU-{index:04d}"
    name = f"{rng.choice(MATERIALS)} {rng.choice(ITEMS)}{rng.choice(VARIANTS)}"
    price_paise = rng.randint(MIN_PRICE_RUPEES, MAX_PRICE_RUPEES) * 100
    stock = 0 if index % SOLD_OUT_EVERY == 0 else rng.randint(1, MAX_STOCK)
    return {"sku": sku, "name": name, "price_paise": price_paise, "stock": stock}


def response_code(response: httpx.Response) -> str:
    """The platform error code from a failed response, or the raw status as a fallback."""
    try:
        body = response.json()
    except ValueError:
        return str(response.status_code)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict) and isinstance(error.get("code"), str):
            return error["code"]
    return str(response.status_code)


def seed(client: httpx.Client, count: int, rng: random.Random) -> tuple[int, int, int]:
    created = skipped = failed = 0
    for index in range(1, count + 1):
        product = build_product(index, rng)
        try:
            response = client.post("/products", json=product)
        except httpx.HTTPError as exc:
            failed += 1
            print(f"  {product['sku']}: {type(exc).__name__}")
            continue
        if response.status_code in (200, 201):
            created += 1
        elif response.status_code == 409:
            skipped += 1
        else:
            failed += 1
            print(f"  {product['sku']}: HTTP {response.status_code} {response_code(response)}")
    return created, skipped, failed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="seed", description="create a product catalogue in inventory-service"
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT)
    parser.add_argument("--seed", type=int, default=None, help="make the catalogue reproducible")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.count < 1:
        print("--count must be at least 1", file=sys.stderr)
        return 2

    rng = random.Random(args.seed)
    base_url = args.base_url.rstrip("/")

    with httpx.Client(base_url=base_url, timeout=args.timeout) as client:
        try:
            client.get("/health")
        except httpx.HTTPError as exc:
            print(f"inventory not reachable at {base_url}: {type(exc).__name__}", file=sys.stderr)
            return 2
        created, skipped, failed = seed(client, args.count, rng)

    print(
        f"{base_url}: {created} created, {skipped} already present, {failed} failed "
        f"(of {args.count})"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
