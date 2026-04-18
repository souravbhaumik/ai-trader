"""Phase 6 — Live trading table.

Tracks real orders placed via Angel One (or other live brokers).
Stores the broker's order ID, status, and links back to the originating signal.

Revision ID: 0007
Revises: 0006
Create Date: 2026-04-25
"""
from __future__ import annotations

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS live_orders (
            id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id         UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            broker_order_id VARCHAR(64),
            symbol          VARCHAR(32) NOT NULL,
            exchange        VARCHAR(8)  NOT NULL DEFAULT 'NSE',
            direction       VARCHAR(4)  NOT NULL,          -- 'BUY' | 'SELL'
            qty             INTEGER     NOT NULL,
            order_type      VARCHAR(16) NOT NULL DEFAULT 'MARKET',
            product_type    VARCHAR(16) NOT NULL DEFAULT 'DELIVERY',
            price           NUMERIC(12,4) NOT NULL DEFAULT 0,
            status          VARCHAR(16) NOT NULL DEFAULT 'PENDING',
            broker_status   VARCHAR(32),
            signal_id       UUID,
            placed_at       TIMESTAMP   NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMP   NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_live_orders_user_id
            ON live_orders (user_id, placed_at DESC)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS live_orders")
