"""Phase 9 intraday: ohlcv_intraday table + Upstox OAuth columns.

Revision ID: 0007_intraday_and_upstox_oauth
Revises: 0006_signal_outcomes_and_improvements
Create Date: 2026-04-21
"""
from __future__ import annotations

from alembic import op

revision = "0007_intraday_and_upstox_oauth"
down_revision = "0006_signal_outcomes_and_improvements"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── ohlcv_intraday (rolling 5-day intraday OHLCV cache) ──────────────────
    # Stores 15-minute candles fetched live from Angel One / Upstox.
    # TimescaleDB retention policy auto-drops data older than 5 days.
    op.execute("""
        CREATE TABLE IF NOT EXISTS ohlcv_intraday (
            symbol      VARCHAR(32)   NOT NULL,
            ts          TIMESTAMPTZ   NOT NULL,
            interval    VARCHAR(10)   NOT NULL DEFAULT '15m',
            open        NUMERIC(14,4) NOT NULL,
            high        NUMERIC(14,4) NOT NULL,
            low         NUMERIC(14,4) NOT NULL,
            close       NUMERIC(14,4) NOT NULL,
            volume      BIGINT        NOT NULL DEFAULT 0,
            source      VARCHAR(20)   NOT NULL DEFAULT 'angel_one',
            PRIMARY KEY (symbol, ts, interval)
        )
    """)

    # TimescaleDB hypertable — partition by time
    op.execute("""
        SELECT create_hypertable(
            'ohlcv_intraday', 'ts',
            if_not_exists => TRUE,
            chunk_time_interval => INTERVAL '1 day'
        )
    """)

    # 5-day auto-retention: data beyond 5 days is automatically dropped
    op.execute("""
        SELECT add_retention_policy(
            'ohlcv_intraday',
            INTERVAL '5 days',
            if_not_exists => TRUE
        )
    """)

    # Index for fast symbol lookups (intraday signal generator)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_ohlcv_intraday_symbol_ts
        ON ohlcv_intraday (symbol, ts DESC)
    """)

    # ── broker_credentials: Upstox OAuth token columns ───────────────────────
    # refresh_token is long-lived (used daily to get a new access_token)
    # access_token is short-lived (Bearer token, refreshed daily at 7:30 AM)
    # access_token_expires_at tracks when to refresh
    op.execute("""
        ALTER TABLE broker_credentials
        ADD COLUMN IF NOT EXISTS refresh_token       TEXT,
        ADD COLUMN IF NOT EXISTS access_token        TEXT,
        ADD COLUMN IF NOT EXISTS access_token_expires_at TIMESTAMPTZ
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS ohlcv_intraday CASCADE")
    op.execute("ALTER TABLE broker_credentials DROP COLUMN IF EXISTS refresh_token")
    op.execute("ALTER TABLE broker_credentials DROP COLUMN IF EXISTS access_token")
    op.execute("ALTER TABLE broker_credentials DROP COLUMN IF EXISTS access_token_expires_at")
