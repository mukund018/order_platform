import uuid
from datetime import UTC, datetime
from enum import Enum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    TypeDecorator,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# Predictable constraint names, so a migration can drop a constraint by name and the
# names in an alembic autogenerate diff match what is already in the database.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    return datetime.now(UTC)


class UtcDateTime(TypeDecorator[datetime]):
    """timestamptz that stays timezone-aware on the way back out.

    Postgres returns an aware datetime; SQLite, which the tests run on, has no timestamp
    type and returns a naive one. The daily report and the expiry job both subtract
    datetimes, and mixing an aware one with a naive one raises instead of being wrong
    quietly - but only on the machine that happens to be running SQLite.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    RESERVED = "RESERVED"
    CONFIRMED = "CONFIRMED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"


class NotificationKind(str, Enum):
    CONFIRMATION = "CONFIRMATION"


# Statuses an order can still leave on its own. Everything else is terminal, which is
# what the expiry job and the stuck-order gauge are both asking about.
UNFINISHED_STATUSES = (OrderStatus.PENDING, OrderStatus.RESERVED)


order_status = SAEnum(OrderStatus, name="order_status")


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        CheckConstraint("total_paise >= 0", name="total_non_negative"),
        # expire_stale_orders and GET /orders?status= both filter on status and then
        # order by age, which is the whole index.
        Index("ix_orders_status_created_at", "status", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    customer_email: Mapped[str] = mapped_column(String(254), nullable=False)
    status: Mapped[OrderStatus] = mapped_column(
        order_status, nullable=False, default=OrderStatus.PENDING
    )
    total_paise: Mapped[int] = mapped_column(Integer, nullable=False)
    # The unique index is what makes a retried POST /orders return the first order
    # instead of placing a second one.
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    failure_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        UtcDateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="OrderItem.sku",
    )
    events: Mapped[list["OrderEvent"]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="OrderEvent.created_at",
    )

    def __repr__(self) -> str:
        return f"<Order id={self.id} status={self.status.value} total_paise={self.total_paise}>"


class OrderItem(Base):
    __tablename__ = "order_items"
    __table_args__ = (
        CheckConstraint("qty > 0", name="qty_positive"),
        # Every read of an order loads its lines by order_id. Postgres does not index a
        # foreign key on its own.
        Index("ix_order_items_order_id", "order_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("orders.id"), nullable=False)
    # inventory_db is a different database, so the product id is a plain column. The sku
    # and the price are copied in on purpose: a report for last month must not change
    # because someone edited the catalogue today.
    product_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    sku: Mapped[str] = mapped_column(String(32), nullable=False)
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price_paise: Mapped[int] = mapped_column(Integer, nullable=False)

    order: Mapped[Order] = relationship(back_populates="items")


class OrderEvent(Base):
    __tablename__ = "order_events"
    __table_args__ = (Index("ix_order_events_order_id_created_at", "order_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("orders.id"), nullable=False)
    from_status: Mapped[OrderStatus | None] = mapped_column(order_status, nullable=True)
    to_status: Mapped[OrderStatus] = mapped_column(order_status, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # The id of the request that caused the transition. It is the only link from a row
    # in this table back to the log lines of the call that wrote it.
    request_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)

    order: Mapped[Order] = relationship(back_populates="events")


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (UniqueConstraint("order_id", "kind", name="uq_notifications_order_id_kind"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("orders.id"), nullable=False)
    kind: Mapped[NotificationKind] = mapped_column(
        SAEnum(NotificationKind, name="notification_kind"), nullable=False
    )
    sent_at: Mapped[datetime] = mapped_column(UtcDateTime, nullable=False, default=utcnow)
