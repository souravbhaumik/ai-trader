"""Add filled_qty and avg_fill_price to live_orders (needed for webhook updates).

Revision ID: 0008
Revises: 0007
Create Date: 2026-04-19
"""
from __future__ import annotations

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE live_orders
            ADD COLUMN IF NOT EXISTS filled_qty     INTEGER       NOT NULL DEFAULT 0,
            ADD COLUMN IF NOT EXISTS avg_fill_price NUMERIC(12,4) NOT NULL DEFAULT 0
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE live_orders
            DROP COLUMN IF EXISTS filled_qty,
            DROP COLUMN IF EXISTS avg_fill_price
    """)
