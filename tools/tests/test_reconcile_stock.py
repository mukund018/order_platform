import httpx
import respx
from reconcile_stock import find_stranded

INVENTORY_URL = "http://inventory.test"
ORDERS_URL = "http://orders.test"


def _active_row(order_id: str, sku: str = "SKU-0001", qty: int = 3) -> dict:
    return {
        "order_id": order_id,
        "sku": sku,
        "qty": qty,
        "created_at": "2026-09-12T08:00:00Z",
    }


@respx.mock
def test_an_active_reservation_for_a_terminal_order_is_stranded() -> None:
    order_id = "11111111-1111-1111-1111-111111111111"
    respx.get(f"{INVENTORY_URL}/reservations/active").mock(
        return_value=httpx.Response(200, json=[_active_row(order_id)])
    )
    respx.get(f"{ORDERS_URL}/orders/{order_id}").mock(
        return_value=httpx.Response(200, json={"status": "CONFIRMED"})
    )

    stranded = find_stranded(INVENTORY_URL, ORDERS_URL, timeout=5.0)

    assert len(stranded) == 1
    assert stranded[0]["order_status"] == "CONFIRMED"


@respx.mock
def test_an_active_reservation_for_a_live_order_is_not_stranded() -> None:
    order_id = "22222222-2222-2222-2222-222222222222"
    respx.get(f"{INVENTORY_URL}/reservations/active").mock(
        return_value=httpx.Response(200, json=[_active_row(order_id)])
    )
    respx.get(f"{ORDERS_URL}/orders/{order_id}").mock(
        return_value=httpx.Response(200, json={"status": "RESERVED"})
    )

    stranded = find_stranded(INVENTORY_URL, ORDERS_URL, timeout=5.0)

    assert stranded == []


@respx.mock
def test_a_reservation_for_an_order_that_no_longer_exists_is_stranded() -> None:
    order_id = "33333333-3333-3333-3333-333333333333"
    respx.get(f"{INVENTORY_URL}/reservations/active").mock(
        return_value=httpx.Response(200, json=[_active_row(order_id)])
    )
    respx.get(f"{ORDERS_URL}/orders/{order_id}").mock(return_value=httpx.Response(404))

    stranded = find_stranded(INVENTORY_URL, ORDERS_URL, timeout=5.0)

    assert len(stranded) == 1
    assert stranded[0]["order_status"] == "MISSING"


@respx.mock
def test_no_active_reservations_means_nothing_stranded() -> None:
    respx.get(f"{INVENTORY_URL}/reservations/active").mock(
        return_value=httpx.Response(200, json=[])
    )

    assert find_stranded(INVENTORY_URL, ORDERS_URL, timeout=5.0) == []
