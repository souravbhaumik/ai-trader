"""Complete initial schema — all tables, hypertables, indexes, and triggers.

Consolidates migrations 0001–0010 into one file.
Run `alembic upgrade head` on a fresh database.

Revision ID: 0001
Revises:
Create Date: 2026-04-20 00:00:00.000000
"""
from __future__ import annotations

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Shared timestamp trigger function ─────────────────────────────────────
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
            id                      UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
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
    op.execute("CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)")
    op.execute("""
        CREATE TRIGGER trg_users_tbl_last_dt
        BEFORE UPDATE ON users
        FOR EACH ROW EXECUTE FUNCTION fn_set_tbl_last_dt()
    """)

    # ── user_settings ─────────────────────────────────────────────────────────
    # notification_news column included inline (was migration 0006)
    op.execute("""
        CREATE TABLE IF NOT EXISTS user_settings (
            user_id                 UUID          PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            trading_mode            VARCHAR(10)   NOT NULL DEFAULT 'paper',
            paper_balance           NUMERIC(15,2) NOT NULL DEFAULT 1000000.00,
            max_position_pct        NUMERIC(5,2)  NOT NULL DEFAULT 10.00,
            daily_loss_limit_pct    NUMERIC(5,2)  NOT NULL DEFAULT 5.00,
            notification_signals    BOOLEAN       NOT NULL DEFAULT TRUE,
            notification_orders     BOOLEAN       NOT NULL DEFAULT TRUE,
            notification_news       BOOLEAN       NOT NULL DEFAULT TRUE,
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
            id          UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
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
    op.execute("CREATE INDEX IF NOT EXISTS idx_user_invites_email ON user_invites(email)")
    op.execute("""
        CREATE TRIGGER trg_user_invites_tbl_last_dt
        BEFORE UPDATE ON user_invites
        FOR EACH ROW EXECUTE FUNCTION fn_set_tbl_last_dt()
    """)

    # ── refresh_tokens ────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS refresh_tokens (
            id          UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id     UUID         NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token_hash  VARCHAR(255) NOT NULL UNIQUE,
            jti         UUID         NOT NULL UNIQUE,
            issued_at   TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
            expires_at  TIMESTAMPTZ  NOT NULL,
            revoked_at  TIMESTAMPTZ,
            user_agent  VARCHAR(255),
            ip_address  VARCHAR(45),
            tbl_last_dt TIMESTAMPTZ  NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_refresh_tokens_user_id ON refresh_tokens(user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_refresh_tokens_jti ON refresh_tokens(jti)")
    op.execute("""
        CREATE TRIGGER trg_refresh_tokens_tbl_last_dt
        BEFORE UPDATE ON refresh_tokens
        FOR EACH ROW EXECUTE FUNCTION fn_set_tbl_last_dt()
    """)

    # ── stock_universe ────────────────────────────────────────────────────────
    # logo_path column included inline (was migration 0009)
    op.execute("""
        CREATE TABLE IF NOT EXISTS stock_universe (
            symbol      VARCHAR(32)  PRIMARY KEY,
            name        VARCHAR(200) NOT NULL DEFAULT '',
            exchange    VARCHAR(10)  NOT NULL DEFAULT 'NSE',
            sector      VARCHAR(100) NOT NULL DEFAULT 'Unknown',
            industry    VARCHAR(100) NOT NULL DEFAULT '',
            market_cap  BIGINT,
            is_etf      BOOLEAN      NOT NULL DEFAULT FALSE,
            is_active   BOOLEAN      NOT NULL DEFAULT TRUE,
            in_nifty50  BOOLEAN      NOT NULL DEFAULT FALSE,
            in_nifty500 BOOLEAN      NOT NULL DEFAULT FALSE,
            logo_path   VARCHAR(300) DEFAULT NULL,
            created_at  TIMESTAMP    NOT NULL DEFAULT NOW(),
            updated_at  TIMESTAMP    NOT NULL DEFAULT NOW(),
            tbl_last_dt TIMESTAMP    NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_universe_sector      ON stock_universe(sector);
        CREATE INDEX IF NOT EXISTS idx_universe_market_cap  ON stock_universe(market_cap DESC NULLS LAST);
        CREATE INDEX IF NOT EXISTS idx_universe_active      ON stock_universe(is_active) WHERE is_active = TRUE;
    """)
    op.execute("""
        CREATE TRIGGER trg_universe_tbl_last_dt
        BEFORE UPDATE ON stock_universe
        FOR EACH ROW EXECUTE FUNCTION fn_set_tbl_last_dt()
    """)

    # ── ohlcv_daily (TimescaleDB hypertable) ──────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS ohlcv_daily (
            symbol  VARCHAR(32)   NOT NULL,
            ts      TIMESTAMP     NOT NULL,
            open    NUMERIC(12,4) NOT NULL,
            high    NUMERIC(12,4) NOT NULL,
            low     NUMERIC(12,4) NOT NULL,
            close   NUMERIC(12,4) NOT NULL,
            volume  BIGINT        NOT NULL DEFAULT 0,
            source  VARCHAR(20)   NOT NULL DEFAULT 'nse',
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
        CREATE INDEX IF NOT EXISTS idx_ohlcv_daily_symbol_ts ON ohlcv_daily (symbol, ts DESC)
    """)
    op.execute("""
        ALTER TABLE ohlcv_daily SET (timescaledb.compress, timescaledb.compress_segmentby = 'symbol')
    """)
    op.execute("""
        SELECT add_compression_policy('ohlcv_daily', INTERVAL '90 days', if_not_exists => TRUE)
    """)

    # ── ohlcv_1min (TimescaleDB hypertable) ───────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS ohlcv_1min (
            symbol  VARCHAR(32)   NOT NULL,
            ts      TIMESTAMP     NOT NULL,
            open    NUMERIC(12,4) NOT NULL,
            high    NUMERIC(12,4) NOT NULL,
            low     NUMERIC(12,4) NOT NULL,
            close   NUMERIC(12,4) NOT NULL,
            volume  BIGINT        NOT NULL DEFAULT 0,
            source  VARCHAR(20)   NOT NULL DEFAULT 'angel_one',
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
        CREATE INDEX IF NOT EXISTS idx_ohlcv_1min_symbol_ts ON ohlcv_1min (symbol, ts DESC)
    """)
    op.execute("""
        ALTER TABLE ohlcv_1min SET (timescaledb.compress, timescaledb.compress_segmentby = 'symbol')
    """)
    op.execute("""
        SELECT add_compression_policy('ohlcv_1min', INTERVAL '7 days', if_not_exists => TRUE)
    """)

    # ── signals (TimescaleDB hypertable) ──────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id            UUID         NOT NULL DEFAULT gen_random_uuid(),
            symbol        VARCHAR(32)  NOT NULL,
            ts            TIMESTAMP    NOT NULL DEFAULT NOW(),
            signal_type   VARCHAR(10)  NOT NULL,
            confidence    NUMERIC(5,4) NOT NULL DEFAULT 0,
            entry_price   NUMERIC(12,4),
            target_price  NUMERIC(12,4),
            stop_loss     NUMERIC(12,4),
            model_version VARCHAR(50)  NOT NULL DEFAULT 'v0',
            features      JSONB,
            is_active     BOOLEAN      NOT NULL DEFAULT TRUE,
            created_at    TIMESTAMP    NOT NULL DEFAULT NOW(),
            PRIMARY KEY (id, ts)
        )
    """)
    op.execute("""
        SELECT create_hypertable('signals', 'ts', if_not_exists => TRUE)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_signals_symbol_ts ON signals (symbol, ts DESC);
        CREATE INDEX IF NOT EXISTS idx_signals_active     ON signals (is_active) WHERE is_active = TRUE;
    """)

    # ── broker_credentials ────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS broker_credentials (
            id            UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id       UUID         NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            broker_name   VARCHAR(20)  NOT NULL,
            client_id     TEXT,
            api_key       TEXT,
            api_secret    TEXT,
            totp_secret   TEXT,
            is_configured BOOLEAN      NOT NULL DEFAULT FALSE,
            last_verified TIMESTAMP,
            created_at    TIMESTAMP    NOT NULL DEFAULT NOW(),
            updated_at    TIMESTAMP    NOT NULL DEFAULT NOW(),
            tbl_last_dt   TIMESTAMP    NOT NULL DEFAULT NOW(),
            UNIQUE (user_id, broker_name)
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_broker_creds_user ON broker_credentials (user_id)
    """)
    op.execute("""
        CREATE TRIGGER trg_broker_creds_tbl_last_dt
        BEFORE UPDATE ON broker_credentials
        FOR EACH ROW EXECUTE FUNCTION fn_set_tbl_last_dt()
    """)

    # ── news_sentiment (TimescaleDB hypertable) ───────────────────────────────
    # summary column included inline (was migration 0010)
    op.execute("""
        CREATE TABLE IF NOT EXISTS news_sentiment (
            id           UUID        NOT NULL DEFAULT gen_random_uuid(),
            published_at TIMESTAMPTZ NOT NULL,
            symbol       VARCHAR(32) NOT NULL,
            headline     TEXT        NOT NULL,
            source       VARCHAR(64) NOT NULL,
            url          TEXT,
            sentiment    VARCHAR(8)  NOT NULL,
            score        REAL        NOT NULL,
            confidence   REAL        NOT NULL,
            summary      TEXT        DEFAULT NULL,
            created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (id, published_at)
        )
    """)
    op.execute("""
        SELECT create_hypertable(
            'news_sentiment', 'published_at',
            chunk_time_interval => INTERVAL '1 day',
            if_not_exists       => TRUE
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_news_sentiment_symbol_ts ON news_sentiment (symbol, published_at DESC);
        CREATE INDEX IF NOT EXISTS ix_news_sentiment_source    ON news_sentiment (source, published_at DESC);
        CREATE INDEX IF NOT EXISTS ix_news_sentiment_url       ON news_sentiment (url, published_at DESC);
    """)

    # ── ml_models ─────────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS ml_models (
            id            UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            model_type    VARCHAR(32) NOT NULL,
            version       VARCHAR(64) NOT NULL,
            artifact_path TEXT        NOT NULL,
            mlflow_run_id VARCHAR(64),
            metrics       JSONB       NOT NULL DEFAULT '{}',
            hyperparams   JSONB       NOT NULL DEFAULT '{}',
            feature_names JSONB       NOT NULL DEFAULT '[]',
            is_active     BOOLEAN     NOT NULL DEFAULT FALSE,
            trained_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            promoted_at   TIMESTAMPTZ,
            promoted_by   UUID        REFERENCES users(id) ON DELETE SET NULL,
            notes         TEXT,
            created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_ml_models_type_active ON ml_models (model_type, is_active);
        CREATE INDEX IF NOT EXISTS ix_ml_models_trained_at  ON ml_models (trained_at DESC);
    """)

    # ── model_predictions (TimescaleDB hypertable) ────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS model_predictions (
            id              UUID        NOT NULL DEFAULT gen_random_uuid(),
            ts              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            symbol          VARCHAR(32) NOT NULL,
            model_id        UUID        NOT NULL REFERENCES ml_models(id) ON DELETE CASCADE,
            direction       VARCHAR(8)  NOT NULL,
            probability     REAL        NOT NULL,
            sentiment_score REAL,
            features_used   JSONB       NOT NULL DEFAULT '{}',
            PRIMARY KEY (id, ts)
        )
    """)
    op.execute("""
        SELECT create_hypertable(
            'model_predictions', 'ts',
            chunk_time_interval => INTERVAL '7 days',
            if_not_exists       => TRUE
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_model_predictions_symbol_ts ON model_predictions (symbol, ts DESC)
    """)

    # ── paper_trades ──────────────────────────────────────────────────────────
    op.execute("""
        CREATE TABLE IF NOT EXISTS paper_trades (
            id          UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id     UUID          NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            symbol      VARCHAR(32)   NOT NULL,
            direction   VARCHAR(8)    NOT NULL,
            qty         INTEGER       NOT NULL,
            entry_price NUMERIC(14,4) NOT NULL,
            target_price NUMERIC(14,4),
            stop_loss   NUMERIC(14,4),
            exit_price  NUMERIC(14,4),
            signal_id   UUID,
            status      VARCHAR(16)   NOT NULL DEFAULT 'open',
            pnl         NUMERIC(14,4),
            pnl_pct     NUMERIC(10,4),
            entry_at    TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
            exit_at     TIMESTAMPTZ,
            notes       TEXT,
            created_at  TIMESTAMPTZ   NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_paper_trades_user_id  ON paper_trades (user_id);
        CREATE INDEX IF NOT EXISTS ix_paper_trades_symbol   ON paper_trades (symbol);
        CREATE INDEX IF NOT EXISTS ix_paper_trades_status   ON paper_trades (user_id, status);
        CREATE INDEX IF NOT EXISTS ix_paper_trades_entry_at ON paper_trades (entry_at DESC);
    """)

    # ── live_orders ───────────────────────────────────────────────────────────
    # filled_qty and avg_fill_price included inline (was migration 0008)
    op.execute("""
        CREATE TABLE IF NOT EXISTS live_orders (
            id              UUID          PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id         UUID          NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            broker_order_id VARCHAR(64),
            symbol          VARCHAR(32)   NOT NULL,
            exchange        VARCHAR(8)    NOT NULL DEFAULT 'NSE',
            direction       VARCHAR(4)    NOT NULL,
            qty             INTEGER       NOT NULL,
            order_type      VARCHAR(16)   NOT NULL DEFAULT 'MARKET',
            product_type    VARCHAR(16)   NOT NULL DEFAULT 'DELIVERY',
            price           NUMERIC(12,4) NOT NULL DEFAULT 0,
            status          VARCHAR(16)   NOT NULL DEFAULT 'PENDING',
            broker_status   VARCHAR(32),
            filled_qty      INTEGER       NOT NULL DEFAULT 0,
            avg_fill_price  NUMERIC(12,4) NOT NULL DEFAULT 0,
            signal_id       UUID,
            placed_at       TIMESTAMP     NOT NULL DEFAULT NOW(),
            updated_at      TIMESTAMP     NOT NULL DEFAULT NOW()
        )
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_live_orders_user_id ON live_orders (user_id, placed_at DESC)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS live_orders CASCADE")
    op.execute("DROP TABLE IF EXISTS paper_trades CASCADE")
    op.execute("DROP TABLE IF EXISTS model_predictions CASCADE")
    op.execute("DROP TABLE IF EXISTS ml_models CASCADE")
    op.execute("DROP TABLE IF EXISTS news_sentiment CASCADE")
    op.execute("DROP TABLE IF EXISTS broker_credentials CASCADE")
    op.execute("DROP TABLE IF EXISTS signals CASCADE")
    op.execute("DROP TABLE IF EXISTS ohlcv_1min CASCADE")
    op.execute("DROP TABLE IF EXISTS ohlcv_daily CASCADE")
    op.execute("DROP TABLE IF EXISTS stock_universe CASCADE")
    op.execute("DROP TABLE IF EXISTS refresh_tokens CASCADE")
    op.execute("DROP TABLE IF EXISTS user_invites CASCADE")
    op.execute("DROP TABLE IF EXISTS user_settings CASCADE")
    op.execute("DROP TABLE IF EXISTS users CASCADE")
    op.execute("DROP FUNCTION IF EXISTS fn_set_tbl_last_dt CASCADE")
