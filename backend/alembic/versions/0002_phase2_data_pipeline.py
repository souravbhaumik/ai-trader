"""Phase 2 — Data pipeline: stock_universe, ohlcv_daily, ohlcv_1min,
signals, broker_credentials tables.

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-17 00:00:00.000000
"""
from __future__ import annotations

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── stock_universe ────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS stock_universe (
            symbol          VARCHAR(32)  PRIMARY KEY,   -- e.g. RELIANCE.NS
            name            VARCHAR(200) NOT NULL DEFAULT '',
            exchange        VARCHAR(10)  NOT NULL DEFAULT 'NSE',
            sector          VARCHAR(100) NOT NULL DEFAULT 'Unknown',
            industry        VARCHAR(100) NOT NULL DEFAULT '',
            market_cap      BIGINT,                     -- in rupees, NULL until enriched
            is_etf          BOOLEAN      NOT NULL DEFAULT FALSE,
            is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
            in_nifty50      BOOLEAN      NOT NULL DEFAULT FALSE,
            in_nifty500     BOOLEAN      NOT NULL DEFAULT FALSE,
            created_at      TIMESTAMP    NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMP    NOT NULL DEFAULT NOW(),
            tbl_last_dt     TIMESTAMP    NOT NULL DEFAULT NOW()
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_universe_sector   ON stock_universe(sector);
        CREATE INDEX IF NOT EXISTS idx_universe_market_cap ON stock_universe(market_cap DESC NULLS LAST);
        CREATE INDEX IF NOT EXISTS idx_universe_active   ON stock_universe(is_active) WHERE is_active = TRUE;
    """)

    op.execute("""
        CREATE TRIGGER trg_universe_tbl_last_dt
        BEFORE UPDATE ON stock_universe
        FOR EACH ROW EXECUTE FUNCTION fn_set_tbl_last_dt()
    """)

    # ── ohlcv_daily ───────────────────────────────────────────────────────────
    # TimescaleDB hypertable — one row per (symbol, date)
    op.execute("""
        CREATE TABLE IF NOT EXISTS ohlcv_daily (
            symbol      VARCHAR(32)      NOT NULL,
            ts          TIMESTAMP        NOT NULL,   -- date at 00:00 IST
            open        NUMERIC(12,4)    NOT NULL,
            high        NUMERIC(12,4)    NOT NULL,
            low         NUMERIC(12,4)    NOT NULL,
            close       NUMERIC(12,4)    NOT NULL,
            volume      BIGINT           NOT NULL DEFAULT 0,
            source      VARCHAR(20)      NOT NULL DEFAULT 'yfinance',
            PRIMARY KEY (symbol, ts)
        )
    """)

    op.execute("""
        SELECT create_hypertable(
            'ohlcv_daily', 'ts',
            partitioning_column => 'symbol',
            number_partitions   => 4,
            if_not_exists       => TRUE
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_ohlcv_daily_symbol_ts
            ON ohlcv_daily (symbol, ts DESC)
    """)

    # ── ohlcv_1min ────────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS ohlcv_1min (
            symbol      VARCHAR(32)      NOT NULL,
            ts          TIMESTAMP        NOT NULL,   -- minute bar start (IST)
            open        NUMERIC(12,4)    NOT NULL,
            high        NUMERIC(12,4)    NOT NULL,
            low         NUMERIC(12,4)    NOT NULL,
            close       NUMERIC(12,4)    NOT NULL,
            volume      BIGINT           NOT NULL DEFAULT 0,
            source      VARCHAR(20)      NOT NULL DEFAULT 'yfinance',
            PRIMARY KEY (symbol, ts)
        )
    """)

    op.execute("""
        SELECT create_hypertable(
            'ohlcv_1min', 'ts',
            partitioning_column => 'symbol',
            number_partitions   => 4,
            if_not_exists       => TRUE
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_ohlcv_1min_symbol_ts
            ON ohlcv_1min (symbol, ts DESC)
    """)

    # Enable TimescaleDB compression on hypertables then apply retention/compression policies
    # Step 1: enable compression (required before add_compression_policy)
    op.execute("""
        ALTER TABLE ohlcv_1min  SET (timescaledb.compress, timescaledb.compress_segmentby = 'symbol')
    """)
    op.execute("""
        ALTER TABLE ohlcv_daily SET (timescaledb.compress, timescaledb.compress_segmentby = 'symbol')
    """)
    # Step 2: attach automatic compression policies
    op.execute("""
        SELECT add_compression_policy('ohlcv_1min',  INTERVAL '7 days',  if_not_exists => TRUE)
    """)
    op.execute("""
        SELECT add_compression_policy('ohlcv_daily', INTERVAL '90 days', if_not_exists => TRUE)
    """)

    # ── signals ───────────────────────────────────────────────────────────────
    # TimescaleDB requires the partitioning column (ts) to be part of the PK.
    # We use (id, ts) composite PK so UUID-based lookups still work.
    op.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id              UUID         NOT NULL DEFAULT gen_random_uuid(),
            symbol          VARCHAR(32)  NOT NULL,
            ts              TIMESTAMP    NOT NULL DEFAULT NOW(),
            signal_type     VARCHAR(10)  NOT NULL,   -- BUY / SELL / HOLD
            confidence      NUMERIC(5,4) NOT NULL DEFAULT 0,  -- 0.0 – 1.0
            entry_price     NUMERIC(12,4),
            target_price    NUMERIC(12,4),
            stop_loss       NUMERIC(12,4),
            model_version   VARCHAR(50)  NOT NULL DEFAULT 'v0',
            features        JSONB,
            is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
            created_at      TIMESTAMP    NOT NULL DEFAULT NOW(),
            PRIMARY KEY (id, ts)
        )
    """)

    op.execute("""
        SELECT create_hypertable(
            'signals', 'ts',
            if_not_exists => TRUE
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_signals_symbol_ts ON signals (symbol, ts DESC);
        CREATE INDEX IF NOT EXISTS idx_signals_active     ON signals (is_active) WHERE is_active = TRUE;
    """)

    # ── broker_credentials ────────────────────────────────────────────────────
    # Sensitive fields stored Fernet-encrypted (same key as TOTP encryption)
    op.execute("""
        CREATE TABLE IF NOT EXISTS broker_credentials (
            id              UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id         UUID         NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            broker_name     VARCHAR(20)  NOT NULL,   -- 'angel_one' | 'upstox' | 'yfinance'
            client_id       TEXT,                    -- Fernet-encrypted
            api_key         TEXT,                    -- Fernet-encrypted
            api_secret      TEXT,                    -- Fernet-encrypted
            totp_secret     TEXT,                    -- Fernet-encrypted (Angel One TOTP)
            is_configured   BOOLEAN      NOT NULL DEFAULT FALSE,
            last_verified   TIMESTAMP,
            created_at      TIMESTAMP    NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMP    NOT NULL DEFAULT NOW(),
            tbl_last_dt     TIMESTAMP    NOT NULL DEFAULT NOW(),
            UNIQUE (user_id, broker_name)
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_broker_creds_user
            ON broker_credentials (user_id)
    """)

    op.execute("""
        CREATE TRIGGER trg_broker_creds_tbl_last_dt
        BEFORE UPDATE ON broker_credentials
        FOR EACH ROW EXECUTE FUNCTION fn_set_tbl_last_dt()
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS broker_credentials CASCADE")
    op.execute("DROP TABLE IF EXISTS signals CASCADE")
    op.execute("DROP TABLE IF EXISTS ohlcv_1min CASCADE")
    op.execute("DROP TABLE IF EXISTS ohlcv_daily CASCADE")
    op.execute("DROP TABLE IF EXISTS stock_universe CASCADE")
