import uuid

import httpx
import pytest

from app.clients.inventory import ReservationLine, get_inventory_client
from app.clients.payments import get_payments_client
from common.errors import AppError, UpstreamError, UpstreamTimeoutError
from common.request_id import bind_request_id, clear_context
from tests.conftest import INVENTORY_URL, Upstream, error_body

LINES = [ReservationLine(sku="SKU-0001", qty=1)]


def reserve() -> None:
    get_inventory_client().reserve(uuid.uuid4(), LINES)


def test_a_timeout_becomes_an_upstream_timeout(upstream: Upstream) -> None:
    upstream.reserve.mock(side_effect=httpx.ReadTimeout("timed out"))

    with pytest.raises(UpstreamTimeoutError) as caught:
        reserve()

    assert caught.value.code == "UPSTREAM_TIMEOUT"
    assert caught.value.status_code == 504
    assert caught.value.details["upstream"] == "inventory"


def test_a_connection_that_is_refused_becomes_an_upstream_error(upstream: Upstream) -> None:
    upstream.reserve.mock(side_effect=httpx.ConnectError("connection refused"))

    with pytest.raises(UpstreamError) as caught:
        reserve()

    assert caught.value.code == "UPSTREAM_ERROR"
    assert caught.value.status_code == 502


def test_an_upstream_error_code_is_kept(upstream: Upstream) -> None:
    upstream.out_of_stock("SKU-0001", available=0, requested=1)

    with pytest.raises(AppError) as caught:
        reserve()

    # The order flow branches on this code, so it has to survive the trip.
    assert caught.value.code == "OUT_OF_STOCK"
    assert caught.value.status_code == 409
    assert caught.value.message == "SKU-0001 has 0 units, 1 requested"


def test_a_server_fault_upstream_becomes_a_502(upstream: Upstream) -> None:
    upstream.reserve.mock(
        return_value=httpx.Response(500, json=error_body("INTERNAL_ERROR", "it broke"))
    )

    with pytest.raises(AppError) as caught:
        reserve()

    # Their 500 is our 502, under our own code: answering INTERNAL_ERROR would send the
    # next person looking for a bug in orders-service. Their code is kept in details.
    assert caught.value.status_code == 502
    assert caught.value.code == "UPSTREAM_ERROR"
    assert caught.value.details["upstream"] == "inventory"
    assert caught.value.details["upstream_code"] == "INTERNAL_ERROR"


def test_a_body_that_is_not_our_envelope_becomes_an_upstream_error(upstream: Upstream) -> None:
    upstream.reserve.mock(return_value=httpx.Response(503, text="<html>gateway</html>"))

    with pytest.raises(UpstreamError) as caught:
        reserve()

    assert caught.value.code == "UPSTREAM_ERROR"


def test_a_success_body_we_cannot_read_becomes_an_upstream_error(upstream: Upstream) -> None:
    upstream.reserve.mock(return_value=httpx.Response(201, json={"unexpected": True}))

    with pytest.raises(UpstreamError):
        reserve()


def test_a_success_body_that_is_not_json_becomes_an_upstream_error(upstream: Upstream) -> None:
    upstream.reserve.mock(return_value=httpx.Response(201, text="OK"))

    with pytest.raises(UpstreamError):
        reserve()


def test_a_product_lookup_returns_the_price(upstream: Upstream) -> None:
    upstream.product("SKU-0007", price_paise=12345)

    product = get_inventory_client().get_product("SKU-0007")

    assert product.sku == "SKU-0007"
    assert product.price_paise == 12345


def test_several_products_come_back_keyed_by_sku(upstream: Upstream) -> None:
    products = get_inventory_client().get_products(["SKU-0001", "SKU-0002"])

    assert sorted(products) == ["SKU-0001", "SKU-0002"]
    assert products["SKU-0002"].price_paise == 4500


def test_the_request_id_goes_out_with_the_call(upstream: Upstream) -> None:
    bind_request_id("client-trace-01")
    try:
        get_inventory_client().get_product("SKU-0001")
    finally:
        clear_context()

    sent = upstream.router.calls.last.request
    assert sent.headers["X-Request-ID"] == "client-trace-01"
    assert str(sent.url).startswith(INVENTORY_URL)


def test_a_charge_reads_back_the_provider_reference(upstream: Upstream) -> None:
    payment = get_payments_client().charge(uuid.uuid4(), 19900)

    assert payment.succeeded
    assert payment.provider_ref == "PAY-0123456789ab"


def test_a_declined_charge_is_a_normal_response_not_an_error(upstream: Upstream) -> None:
    upstream.decline("insufficient_funds")

    payment = get_payments_client().charge(uuid.uuid4(), 19900)

    assert not payment.succeeded
    assert payment.failure_reason == "insufficient_funds"
