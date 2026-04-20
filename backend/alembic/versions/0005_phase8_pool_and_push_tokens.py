"""Phase 8: broker pool flag + expo push tokens table.

Revision ID: 0005_phase8_pool_and_push_tokens
Revises: 0004_add_dedup_hash_and_indexes
Create Date: 2026-04-21
"""
from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "0005_phase8_pool_and_push_tokens"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE broker_credentials
        ADD COLUMN IF NOT EXISTS pool_eligible BOOLEAN NOT NULL DEFAULT FALSE
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS expo_push_tokens (
            id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id       UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token         VARCHAR(200) NOT NULL,
            device_id     VARCHAR(100) NOT NULL,
            platform      VARCHAR(10) NOT NULL,
            is_active     BOOLEAN NOT NULL DEFAULT TRUE,
            registered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_used_at  TIMESTAMPTZ,
            tbl_last_dt   TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )

    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uniq_expo_token_active
            ON expo_push_tokens (token)
            WHERE is_active = TRUE
        """
    )
    op.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS uniq_expo_device_active
            ON expo_push_tokens (device_id)
            WHERE is_active = TRUE
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_expo_tokens_user
            ON expo_push_tokens (user_id)
            WHERE is_active = TRUE
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS expo_push_tokens CASCADE")
    op.execute("ALTER TABLE broker_credentials DROP COLUMN IF EXISTS pool_eligible")
