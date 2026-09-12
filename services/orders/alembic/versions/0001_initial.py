"""orders, items, events and notifications

Revision ID: 0001
Revises:
Create Date: 2025-11-12

"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# Two tables use order_status. Attaching the types to a MetaData of their own takes
# their DDL out of the hands of create_table / drop_table, which on Postgres would
# otherwise CREATE TYPE twice and, worse, DROP TYPE after the first of the two tables
# went away while the second still depended on it. Created and dropped explicitly below.
types = sa.MetaData()

order_status = sa.Enum(
    "PENDING",
    "RESERVED",
    "CONFIRMED",
    "FAILED",
    "EXPIRED",
    "CANCELLED",
    name="order_status",
    metadata=types,
)

notification_kind = sa.Enum("CONFIRMATION", name="notification_kind", metadata=types)


def upgrade() -> None:
    bind = op.get_bind()
    order_status.create(bind, checkfirst=True)
    notification_kind.create(bind, checkfirst=True)

    op.create_table(
        "orders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("customer_email", sa.String(length=254), nullable=False),
        sa.Column("status", order_status, nullable=False),
        sa.Column("total_paise", sa.Integer(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("failure_reason", sa.String(length=200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_orders"),
        sa.UniqueConstraint("idempotency_key", name="uq_orders_idempotency_key"),
        sa.CheckConstraint("total_paise >= 0", name="ck_orders_total_non_negative"),
    )
    op.create_index("ix_orders_status_created_at", "orders", ["status", "created_at"])

    op.create_table(
        "order_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("sku", sa.String(length=32), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("unit_price_paise", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_order_items"),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"], name="fk_order_items_order_id_orders"
        ),
        sa.CheckConstraint("qty > 0", name="ck_order_items_qty_positive"),
    )
    op.create_index("ix_order_items_order_id", "order_items", ["order_id"])

    op.create_table(
        "order_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("from_status", order_status, nullable=True),
        sa.Column("to_status", order_status, nullable=False),
        sa.Column("reason", sa.String(length=200), nullable=True),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_order_events"),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"], name="fk_order_events_order_id_orders"
        ),
    )
    op.create_index(
        "ix_order_events_order_id_created_at", "order_events", ["order_id", "created_at"]
    )

    op.create_table(
        "notifications",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("kind", notification_kind, nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_notifications"),
        sa.ForeignKeyConstraint(
            ["order_id"], ["orders.id"], name="fk_notifications_order_id_orders"
        ),
        # The constraint the confirmation task relies on to stay idempotent under retry.
        sa.UniqueConstraint("order_id", "kind", name="uq_notifications_order_id_kind"),
    )


def downgrade() -> None:
    op.drop_table("notifications")
    op.drop_index("ix_order_events_order_id_created_at", table_name="order_events")
    op.drop_table("order_events")
    op.drop_index("ix_order_items_order_id", table_name="order_items")
    op.drop_table("order_items")
    op.drop_index("ix_orders_status_created_at", table_name="orders")
    op.drop_table("orders")

    bind = op.get_bind()
    notification_kind.drop(bind, checkfirst=True)
    order_status.drop(bind, checkfirst=True)
