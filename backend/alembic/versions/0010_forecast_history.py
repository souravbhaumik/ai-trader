"""Add forecast_history table for PatchTST / TFT self-evaluation.

Stores one row per (symbol, model_version, forecast_date).
The evaluator task fills actual_prices and computes RMSE / MAE
the morning after the forecast horizon expires.

Revision ID: 0010_forecast_history
Revises: 0009_signals_unique_constraint
Create Date: 2026-05-02
"""
from __future__ import annotations

from alembic import op

revision = "0010_forecast_history"
down_revision = "0009_signals_unique_constraint"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS forecast_history (
            id              UUID        NOT NULL DEFAULT gen_random_uuid(),
            symbol          VARCHAR(32) NOT NULL,
            model_version   VARCHAR(64) NOT NULL,
            model_type      VARCHAR(16) NOT NULL DEFAULT 'patchtst',
            forecast_date   DATE        NOT NULL,
            base_price      NUMERIC(12,4) NOT NULL,
            horizon_days    SMALLINT    NOT NULL DEFAULT 5,

            -- Predicted prices for days +1 … +horizon (JSONB array of floats)
            predicted_prices JSONB      NOT NULL DEFAULT '[]',

            -- Actual closing prices filled by evaluate_forecast_accuracy task
            -- NULL until the full horizon has passed
            actual_prices   JSONB,

            -- Accuracy metrics (filled by evaluator task)
            rmse            NUMERIC(12,6),
            mae             NUMERIC(12,6),
            directional_acc NUMERIC(5,4),    -- fraction of correct direction calls

            -- Whether the horizon has fully passed and actuals are available
            is_evaluated    BOOLEAN     NOT NULL DEFAULT FALSE,

            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            evaluated_at    TIMESTAMPTZ,

            PRIMARY KEY (id, forecast_date),
            UNIQUE (symbol, model_version, forecast_date)
        )
    """)

    # TimescaleDB hypertable on forecast_date for efficient range queries
    op.execute("""
        SELECT create_hypertable(
            'forecast_history', 'forecast_date',
            chunk_time_interval => INTERVAL '3 months',
            if_not_exists       => TRUE
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_forecast_history_symbol_date
            ON forecast_history (symbol, forecast_date DESC);
        CREATE INDEX IF NOT EXISTS ix_forecast_history_model_version
            ON forecast_history (model_version, forecast_date DESC);
        CREATE INDEX IF NOT EXISTS ix_forecast_history_unevaluated
            ON forecast_history (is_evaluated, forecast_date)
            WHERE is_evaluated = FALSE;
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS forecast_history CASCADE")
