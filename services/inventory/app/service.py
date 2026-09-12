"""Product and reservation domain logic.

The caller owns the transaction: nothing here commits. An endpoint calls one of these
functions and commits once, so a failure half way through a multi-item reservation
leaves the database exactly as it was.
"""

import uuid
from collections.abc import Iterable, Sequence

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app.cache import ProductCache
from app.metrics import RESERVATION_FAILURES
from app.models import Product, ReservationStatus, StockReservation
from app.schemas import ProductCreate, ProductOut, ReservationItem
from common.errors import ConflictError, NotFoundError, OutOfStockError, ValidationFailedError
from common.logging import get_logger

log = get_logger(__name__)


def list_products(session: Session, cache: ProductCache) -> list[ProductOut]:
    cached = cache.get_list()
    if cached is not None:
        return [ProductOut.model_validate(row) for row in cached]

    products = session.scalars(select(Product).order_by(Product.sku)).all()
    result = [ProductOut.model_validate(product) for product in products]
    cache.set_list([product.model_dump(mode="json") for product in result])
    return result


def get_product(session: Session, sku: str, cache: ProductCache) -> ProductOut:
    cached = cache.get_product(sku)
    if cached is not None:
        return ProductOut.model_validate(cached)

    result = ProductOut.model_validate(_require_product(session, sku))
    cache.set_product(sku, result.model_dump(mode="json"))
    return result


def create_product(session: Session, payload: ProductCreate) -> Product:
    product = Product(
        sku=payload.sku,
        name=payload.name,
        price_paise=payload.price_paise,
        stock=payload.stock,
    )
    session.add(product)
    try:
        session.flush()
    except IntegrityError as exc:
        # The unique index is the real check; reading first would only race with it.
        session.rollback()
        raise ConflictError(
            f"sku {payload.sku} already exists", details={"sku": payload.sku}
        ) from exc

    log.info("product_created", sku=product.sku, product_id=str(product.id), stock=product.stock)
    return product


def adjust_stock(session: Session, sku: str, delta: int) -> Product:
    product = _require_product(session, sku)
    if delta == 0:
        return product

    statement = (
        update(Product)
        .where(Product.id == product.id)
        .values(stock=Product.stock + delta)
        .execution_options(synchronize_session=False)
    )
    if delta < 0:
        statement = statement.where(Product.stock >= -delta)

    if session.execute(statement).rowcount == 0:
        raise OutOfStockError(
            f"{sku} has {product.stock} units, cannot remove {-delta}",
            details={"sku": sku, "available": product.stock, "requested": -delta},
        )

    session.refresh(product)
    log.info("stock_adjusted", sku=sku, delta=delta, stock=product.stock)
    return product


def reserve(
    session: Session, order_id: uuid.UUID, items: Sequence[ReservationItem]
) -> list[StockReservation]:
    """Reserve every line or none of them."""
    skus = [item.sku for item in items]
    duplicates = sorted({sku for sku in skus if skus.count(sku) > 1})
    if duplicates:
        raise ValidationFailedError(
            f"duplicate sku in request: {', '.join(duplicates)}", details={"skus": duplicates}
        )

    live = _reservations_for(
        session, order_id, statuses=(ReservationStatus.ACTIVE, ReservationStatus.COMMITTED)
    )
    if live:
        # orders-service retries this call after a timeout, and a retry must not take
        # stock a second time.
        log.info("reservation_replayed", order_id=str(order_id), items=len(live))
        return live

    products = {
        product.sku: product
        for product in session.scalars(select(Product).where(Product.sku.in_(skus)))
    }
    unknown = sorted(set(skus) - set(products))
    if unknown:
        RESERVATION_FAILURES.labels(reason="unknown_sku").inc()
        raise NotFoundError(
            f"unknown sku: {', '.join(unknown)}", code="UNKNOWN_SKU", details={"skus": unknown}
        )

    reservations: list[StockReservation] = []
    for item in items:
        product = products[item.sku]
        updated = session.execute(
            update(Product)
            .where(Product.id == product.id, Product.stock >= item.qty)
            .values(stock=Product.stock - item.qty)
            .execution_options(synchronize_session=False)
        )
        if updated.rowcount == 0:
            available = _current_stock(session, product.id)
            RESERVATION_FAILURES.labels(reason="out_of_stock").inc()
            log.warning(
                "reservation_rejected",
                order_id=str(order_id),
                sku=item.sku,
                requested=item.qty,
                available=available,
            )
            raise OutOfStockError(
                f"{item.sku} has {available} units, {item.qty} requested",
                details={"sku": item.sku, "available": available, "requested": item.qty},
            )
        reservations.append(
            StockReservation(
                order_id=order_id,
                product=product,
                qty=item.qty,
                status=ReservationStatus.ACTIVE,
            )
        )

    session.add_all(reservations)
    session.flush()
    log.info("stock_reserved", order_id=str(order_id), items=len(reservations))
    return reservations


def commit_reservation(session: Session, order_id: uuid.UUID) -> list[StockReservation]:
    rows = _reservations_for(session, order_id)
    if not rows:
        raise NotFoundError(
            f"no reservation for order {order_id}",
            code="UNKNOWN_RESERVATION",
            details={"order_id": str(order_id)},
        )
    if any(row.status is ReservationStatus.RELEASED for row in rows):
        raise ConflictError(
            f"reservation for order {order_id} was already released",
            details={"order_id": str(order_id)},
        )

    for row in rows:
        row.status = ReservationStatus.COMMITTED
    session.flush()
    log.info("reservation_committed", order_id=str(order_id), items=len(rows))
    return rows


def release_reservation(session: Session, order_id: uuid.UUID) -> list[StockReservation]:
    """Return the stock and mark the rows RELEASED. A no-op if there is nothing to do."""
    rows = _reservations_for(session, order_id)
    returned = 0
    for row in _in_sku_order(rows):
        if row.status is ReservationStatus.RELEASED:
            continue
        session.execute(
            update(Product)
            .where(Product.id == row.product_id)
            .values(stock=Product.stock + row.qty)
            .execution_options(synchronize_session=False)
        )
        row.status = ReservationStatus.RELEASED
        returned += row.qty

    session.flush()
    log.info("reservation_released", order_id=str(order_id), items=len(rows), units=returned)
    return rows


def list_active_reservations(session: Session) -> list[StockReservation]:
    """Every reservation still holding stock against an order. INC-009: nothing on
    inventory's side knows whether the order on the other end of one of these rows is
    still alive - that has to be asked of orders-service, which is what reconciliation
    is for."""
    statement = (
        select(StockReservation)
        .where(StockReservation.status == ReservationStatus.ACTIVE)
        .options(selectinload(StockReservation.product))
        .order_by(StockReservation.created_at)
    )
    return list(session.scalars(statement))


def _require_product(session: Session, sku: str) -> Product:
    product = session.scalar(select(Product).where(Product.sku == sku))
    if product is None:
        raise NotFoundError(f"no product with sku {sku}", details={"sku": sku})
    return product


def _current_stock(session: Session, product_id: uuid.UUID) -> int:
    """Re-read stock for an error message: the row we loaded may already be stale."""
    stock = session.scalar(select(Product.stock).where(Product.id == product_id))
    return stock if stock is not None else 0


def _reservations_for(
    session: Session,
    order_id: uuid.UUID,
    statuses: Sequence[ReservationStatus] | None = None,
) -> list[StockReservation]:
    statement = select(StockReservation).where(StockReservation.order_id == order_id)
    if statuses is not None:
        statement = statement.where(StockReservation.status.in_(statuses))
    return list(session.scalars(statement))


def _in_sku_order(rows: Iterable[StockReservation]) -> list[StockReservation]:
    """Same fixed ordering as reserve(), for the same deadlock reason."""
    return sorted(rows, key=lambda row: row.product.sku)
