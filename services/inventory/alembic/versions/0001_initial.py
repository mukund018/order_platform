"""products and stock reservations

Revision ID: 0001
Revises:
Create Date: 2025-11-03

"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

reservation_status = sa.Enum("ACTIVE", "COMMITTED", "RELEASED", name="reservation_status")


def upgrade() -> None:
    op.create_table(
        "products",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("sku", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("price_paise", sa.Integer(), nullable=False),
        sa.Column("stock", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_products"),
        sa.UniqueConstraint("sku", name="uq_products_sku"),
        sa.CheckConstraint("price_paise > 0", name="ck_products_price_positive"),
        sa.CheckConstraint("stock >= 0", name="ck_products_stock_non_negative"),
    )

    op.create_table(
        "stock_reservations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("product_id", sa.Uuid(), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("status", reservation_status, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_stock_reservations"),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["products.id"],
            name="fk_stock_reservations_product_id_products",
        ),
        sa.CheckConstraint("qty > 0", name="ck_stock_reservations_qty_positive"),
    )
    op.create_index("ix_stock_reservations_order_id", "stock_reservations", ["order_id"])


def downgrade() -> None:
    op.drop_index("ix_stock_reservations_order_id", table_name="stock_reservations")
    op.drop_table("stock_reservations")
    op.drop_table("products")
    reservation_status.drop(op.get_bind(), checkfirst=True)
