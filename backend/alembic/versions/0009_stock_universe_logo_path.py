"""Add logo_path column to stock_universe.

Revision ID: 0009
Revises: 0008
Create Date: 2026-04-19
"""
from __future__ import annotations

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE stock_universe
            ADD COLUMN IF NOT EXISTS logo_path VARCHAR(300) DEFAULT NULL
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE stock_universe
            DROP COLUMN IF EXISTS logo_path
    """)
