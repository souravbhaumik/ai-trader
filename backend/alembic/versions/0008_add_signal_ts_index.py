"""Add plain signal_ts index on signal_outcomes for analytics cutoff queries.

The analytics service runs queries such as:
    WHERE signal_ts >= :cutoff
against signal_outcomes.  Migration 0006 created composite indexes on
(symbol, signal_ts DESC) and (is_evaluated, signal_ts DESC) but no standalone
signal_ts index, so range scans on signal_ts alone require a full-table scan or
rely on the planner choosing the composite index inefficiently.

Revision ID: 0008_add_signal_ts_index
Revises: 0007_intraday_and_upstox_oauth
Create Date: 2026-04-22
"""
from __future__ import annotations

from alembic import op

revision = "0008_add_signal_ts_index"
down_revision = "0007_intraday_and_upstox_oauth"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_signal_outcomes_signal_ts "
        "ON signal_outcomes (signal_ts DESC)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_signal_outcomes_signal_ts")
