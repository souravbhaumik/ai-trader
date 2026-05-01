"""Add UNIQUE constraint on signals(symbol, ts, signal_type) for intraday upserts.

The intraday signal generator uses:
    ON CONFLICT (symbol, ts, signal_type) DO UPDATE SET ...

Without a UNIQUE or EXCLUSION constraint on those columns PostgreSQL raises:
    InvalidColumnReference: there is no unique or exclusion constraint
    matching the ON CONFLICT specification

This migration adds the constraint so intraday (and daily) signal upserts
work correctly without duplicating rows.

Revision ID: 0009_signals_unique_constraint
Revises: 0008_add_signal_ts_index
Create Date: 2026-04-27
"""
from __future__ import annotations

from alembic import op

revision = "0009_signals_unique_constraint"
down_revision = "0008_add_signal_ts_index"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Deduplicate any existing rows that would violate the new constraint.
    # Keep the row with the highest confidence for each (symbol, ts, signal_type) group.
    op.execute("""
        DELETE FROM signals
        WHERE id NOT IN (
            SELECT DISTINCT ON (symbol, ts, signal_type) id
            FROM signals
            ORDER BY symbol, ts, signal_type, confidence DESC
        )
    """)

    # Add the unique constraint
    op.execute("""
        ALTER TABLE signals
        ADD CONSTRAINT uq_signals_symbol_ts_type
        UNIQUE (symbol, ts, signal_type)
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE signals
        DROP CONSTRAINT IF EXISTS uq_signals_symbol_ts_type
    """)
