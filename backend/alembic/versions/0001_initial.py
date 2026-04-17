"""Phase 1 initial schema — users, user_settings, user_invites, refresh_tokens.

Revision ID: 0001
Revises:
Create Date: 2025-01-01 00:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Create generic timestamp trigger function ─────────────────────────────
    op.execute("""
        CREATE OR REPLACE FUNCTION fn_set_tbl_last_dt()
        RETURNS TRIGGER LANGUAGE plpgsql AS $$
        BEGIN
            NEW.tbl_last_dt = NOW();
            RETURN NEW;
        END;
        $$;
    """)

    # ── users ─────────────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            email                   VARCHAR(255) NOT NULL UNIQUE,
            hashed_password         VARCHAR(255) NOT NULL,
            full_name               VARCHAR(100),
            role                    VARCHAR(20)  NOT NULL DEFAULT 'trader',
            is_active               BOOLEAN      NOT NULL DEFAULT TRUE,
            is_email_verified       BOOLEAN      NOT NULL DEFAULT FALSE,
            is_live_trading_enabled BOOLEAN      NOT NULL DEFAULT FALSE,
            totp_secret             VARCHAR(255),
            is_totp_configured      BOOLEAN      NOT NULL DEFAULT FALSE,
            invited_by              UUID         REFERENCES users(id) ON DELETE SET NULL,
            last_login_at           TIMESTAMPTZ,
            created_at              TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            updated_at              TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            tbl_last_dt             TIMESTAMPTZ  NOT NULL DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)
    """)

    op.execute("""
        CREATE TRIGGER trg_users_tbl_last_dt
        BEFORE UPDATE ON users
        FOR EACH ROW EXECUTE FUNCTION fn_set_tbl_last_dt()
    """)

    # ── user_settings ─────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS user_settings (
            user_id                 UUID        PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            trading_mode            VARCHAR(10) NOT NULL DEFAULT 'paper',
            paper_balance           NUMERIC(15,2) NOT NULL DEFAULT 1000000.00,
            max_position_pct        NUMERIC(5,2)  NOT NULL DEFAULT 10.00,
            daily_loss_limit_pct    NUMERIC(5,2)  NOT NULL DEFAULT 5.00,
            notification_signals    BOOLEAN       NOT NULL DEFAULT TRUE,
            notification_orders     BOOLEAN       NOT NULL DEFAULT TRUE,
            preferred_broker        VARCHAR(20),
            updated_at              TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
            tbl_last_dt             TIMESTAMPTZ   NOT NULL DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE TRIGGER trg_user_settings_tbl_last_dt
        BEFORE UPDATE ON user_settings
        FOR EACH ROW EXECUTE FUNCTION fn_set_tbl_last_dt()
    """)

    # ── user_invites ──────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS user_invites (
            id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            email       VARCHAR(255) NOT NULL,
            token_hash  VARCHAR(255) NOT NULL UNIQUE,
            status      VARCHAR(20)  NOT NULL DEFAULT 'pending',
            invited_by  UUID         NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            user_id     UUID         REFERENCES users(id) ON DELETE SET NULL,
            expires_at  TIMESTAMPTZ  NOT NULL,
            used_at     TIMESTAMPTZ,
            revoked_at  TIMESTAMPTZ,
            created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            tbl_last_dt TIMESTAMPTZ  NOT NULL DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_user_invites_email ON user_invites(email)
    """)

    op.execute("""
        CREATE TRIGGER trg_user_invites_tbl_last_dt
        BEFORE UPDATE ON user_invites
        FOR EACH ROW EXECUTE FUNCTION fn_set_tbl_last_dt()
    """)

    # ── refresh_tokens ────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS refresh_tokens (
            id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id     UUID        NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token_hash  VARCHAR(255) NOT NULL UNIQUE,
            jti         UUID        NOT NULL UNIQUE,
            issued_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            expires_at  TIMESTAMPTZ NOT NULL,
            revoked_at  TIMESTAMPTZ,
            user_agent  VARCHAR(255),
            ip_address  VARCHAR(45),
            tbl_last_dt TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_refresh_tokens_user_id ON refresh_tokens(user_id)
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_refresh_tokens_jti ON refresh_tokens(jti)
    """)

    op.execute("""
        CREATE TRIGGER trg_refresh_tokens_tbl_last_dt
        BEFORE UPDATE ON refresh_tokens
        FOR EACH ROW EXECUTE FUNCTION fn_set_tbl_last_dt()
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS refresh_tokens CASCADE")
    op.execute("DROP TABLE IF EXISTS user_invites CASCADE")
    op.execute("DROP TABLE IF EXISTS user_settings CASCADE")
    op.execute("DROP TABLE IF EXISTS users CASCADE")
