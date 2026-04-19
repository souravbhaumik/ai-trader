"""Add summary column to news_sentiment.

Revision ID: 0010
Revises: 0009
Create Date: 2026-04-19
"""
from __future__ import annotations

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE news_sentiment
            ADD COLUMN IF NOT EXISTS summary TEXT DEFAULT NULL
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE news_sentiment
            DROP COLUMN IF EXISTS summary
    """)
