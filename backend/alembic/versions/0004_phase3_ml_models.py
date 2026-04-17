"""Phase 3 — ML model registry table.

Tracks trained model versions, their artifacts, and which version is
currently active for live inference.

Revision ID: 0004
Revises: 0003
Create Date: 2026-04-17
"""
from __future__ import annotations

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── ml_models ─────────────────────────────────────────────────────────────
    # One row per trained model version.  The "active" flag controls which
    # version the signal generator loads at startup / on reload.
    op.execute("""
        CREATE TABLE IF NOT EXISTS ml_models (
            id              UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
            model_type      VARCHAR(32) NOT NULL,   -- 'lgbm' | 'lstm' | 'tft'
            version         VARCHAR(64) NOT NULL,   -- e.g. 'lgbm-v3'
            artifact_path   TEXT        NOT NULL,   -- local FS path or S3/B2 URI
            mlflow_run_id   VARCHAR(64),            -- MLflow run tracking ID (optional)
            metrics         JSONB       NOT NULL DEFAULT '{}',
            hyperparams     JSONB       NOT NULL DEFAULT '{}',
            feature_names   JSONB       NOT NULL DEFAULT '[]',  -- ordered list
            is_active       BOOLEAN     NOT NULL DEFAULT FALSE,
            trained_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            promoted_at     TIMESTAMPTZ,
            promoted_by     UUID        REFERENCES users(id) ON DELETE SET NULL,
            notes           TEXT,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_ml_models_type_active
            ON ml_models (model_type, is_active);

        CREATE INDEX IF NOT EXISTS ix_ml_models_trained_at
            ON ml_models (trained_at DESC);
    """)

    # ── model_predictions ─────────────────────────────────────────────────────
    # Stores per-symbol ML prediction output — joins with signals for auditability.
    op.execute("""
        CREATE TABLE IF NOT EXISTS model_predictions (
            id              UUID        NOT NULL DEFAULT gen_random_uuid(),
            ts              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            symbol          VARCHAR(32) NOT NULL,
            model_id        UUID        NOT NULL REFERENCES ml_models(id) ON DELETE CASCADE,
            direction       VARCHAR(8)  NOT NULL,  -- 'BUY' | 'SELL' | 'HOLD'
            probability     REAL        NOT NULL,  -- model confidence [0,1]
            sentiment_score REAL,                  -- Phase 4 Redis cache at inference time
            features_used   JSONB       NOT NULL DEFAULT '{}',
            PRIMARY KEY (id, ts)
        );
    """)

    op.execute("""
        SELECT create_hypertable(
            'model_predictions', 'ts',
            chunk_time_interval => INTERVAL '7 days',
            if_not_exists       => TRUE
        );
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_model_predictions_symbol_ts
            ON model_predictions (symbol, ts DESC);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS model_predictions;")
    op.execute("DROP TABLE IF EXISTS ml_models;")
