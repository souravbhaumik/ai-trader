"""Phase 5 — Paper trading table.

Tracks simulated trades placed automatically from signals or manually by users.
Stores entry/exit prices, P&L, and links back to the originating signal.

Revision ID: 0005
Revises: 0004
Create Date: 2026-04-18
"""
from __future__ import annotations

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS paper_trades (
            id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id         UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            symbol          VARCHAR(32) NOT NULL,
            direction       VARCHAR(8)  NOT NULL,          -- 'BUY' | 'SELL'
            qty             INTEGER     NOT NULL,
            entry_price     NUMERIC(14,4) NOT NULL,
            target_price    NUMERIC(14,4),
            stop_loss       NUMERIC(14,4),
            exit_price      NUMERIC(14,4),
            signal_id       UUID,                          -- soft ref to signals.id
            status          VARCHAR(16) NOT NULL DEFAULT 'open',
                                                           -- 'open' | 'closed' | 'sl_hit' | 'target_hit' | 'cancelled'
            pnl             NUMERIC(14,4),                 -- realised P&L (null while open)
            pnl_pct         NUMERIC(10,4),                 -- pnl as % of entry value
            entry_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            exit_at         TIMESTAMPTZ,
            notes           TEXT,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );

        CREATE INDEX IF NOT EXISTS ix_paper_trades_user_id
            ON paper_trades (user_id);
        CREATE INDEX IF NOT EXISTS ix_paper_trades_symbol
            ON paper_trades (symbol);
        CREATE INDEX IF NOT EXISTS ix_paper_trades_status
            ON paper_trades (user_id, status);
        CREATE INDEX IF NOT EXISTS ix_paper_trades_entry_at
            ON paper_trades (entry_at DESC);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS paper_trades;")
