"""Add notification_news column to user_settings.

Revision ID: 0006
Revises: 0005
Create Date: 2026-04-18
"""
from __future__ import annotations

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE user_settings
        ADD COLUMN IF NOT EXISTS notification_news BOOLEAN NOT NULL DEFAULT TRUE
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE user_settings
        DROP COLUMN IF EXISTS notification_news
    """)
