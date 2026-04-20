"""Add explanation column to signals table.

Adds a nullable TEXT column ``explanation`` to the ``signals`` hypertable.
Populated asynchronously by the ``explain_signal`` Celery task after signal
generation.  NULL means "not yet explained" — the frontend hides the
explanation card when the column is NULL.

Revision ID: 0003
Revises: 0002
Create Date: 2026-05-01
"""
from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Nullable TEXT — no default needed (NULL = pending / no explanation)
    op.execute("""
        ALTER TABLE signals
            ADD COLUMN IF NOT EXISTS explanation TEXT
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE signals
            DROP COLUMN IF EXISTS explanation
    """)
