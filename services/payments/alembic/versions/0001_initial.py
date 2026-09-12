"""payments table

Revision ID: 0001
Revises:
Create Date: 2025-01-01
"""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "payments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("amount_paise", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("PENDING", "SUCCEEDED", "FAILED", name="payment_status"),
            nullable=False,
        ),
        sa.Column("provider_ref", sa.String(length=64), nullable=True),
        sa.Column("failure_reason", sa.String(length=200), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("amount_paise > 0", name="ck_payments_amount_paise_positive"),
        sa.CheckConstraint("attempts >= 0", name="ck_payments_attempts_non_negative"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id", name="uq_payments_order_id"),
    )


def downgrade() -> None:
    op.drop_table("payments")
    sa.Enum(name="payment_status").drop(op.get_bind(), checkfirst=True)
