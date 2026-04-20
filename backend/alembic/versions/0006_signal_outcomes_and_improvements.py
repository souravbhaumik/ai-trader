"""Phase 9: Signal outcomes tracking, slippage, market hours setting.

Revision ID: 0006_signal_outcomes_and_improvements
Revises: 0005_phase8_pool_and_push_tokens
Create Date: 2026-04-21
"""
from __future__ import annotations

from alembic import op

revision = "0006_signal_outcomes_and_improvements"
down_revision = "0005_phase8_pool_and_push_tokens"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── signal_outcomes table ─────────────────────────────────────────────────
    # Dedicated table for tracking signal performance over time
    op.execute("""
        CREATE TABLE IF NOT EXISTS signal_outcomes (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            signal_id       UUID NOT NULL,
            symbol          VARCHAR(32) NOT NULL,
            signal_type     VARCHAR(10) NOT NULL,
            signal_ts       TIMESTAMPTZ NOT NULL,
            entry_price     NUMERIC(14,4) NOT NULL,
            target_price    NUMERIC(14,4),
            stop_loss       NUMERIC(14,4),
            confidence      NUMERIC(5,4) NOT NULL,
            
            -- Outcome tracking
            price_1d        NUMERIC(14,4),
            price_3d        NUMERIC(14,4),
            price_5d        NUMERIC(14,4),
            return_1d_pct   NUMERIC(8,4),
            return_3d_pct   NUMERIC(8,4),
            return_5d_pct   NUMERIC(8,4),
            
            -- Target/SL tracking
            hit_target      BOOLEAN DEFAULT FALSE,
            hit_stoploss    BOOLEAN DEFAULT FALSE,
            hit_target_at   TIMESTAMPTZ,
            hit_stoploss_at TIMESTAMPTZ,
            max_gain_pct    NUMERIC(8,4),
            max_drawdown_pct NUMERIC(8,4),
            
            -- Evaluation status
            is_evaluated    BOOLEAN DEFAULT FALSE,
            evaluated_at    TIMESTAMPTZ,
            
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            tbl_last_dt     TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
    """)
    
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_signal_outcomes_signal_id 
        ON signal_outcomes (signal_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_signal_outcomes_symbol_ts 
        ON signal_outcomes (symbol, signal_ts DESC)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS idx_signal_outcomes_evaluated 
        ON signal_outcomes (is_evaluated, signal_ts DESC)
    """)
    op.execute("""
        CREATE TRIGGER trg_signal_outcomes_tbl_last_dt
        BEFORE UPDATE ON signal_outcomes
        FOR EACH ROW EXECUTE FUNCTION fn_set_tbl_last_dt()
    """)
    
    # ── user_settings new columns ─────────────────────────────────────────────
    # Market hours validation toggle (user can disable if they want)
    op.execute("""
        ALTER TABLE user_settings
        ADD COLUMN IF NOT EXISTS enforce_market_hours BOOLEAN NOT NULL DEFAULT TRUE
    """)
    
    # Max sector exposure percentage (for diversification warning)
    op.execute("""
        ALTER TABLE user_settings
        ADD COLUMN IF NOT EXISTS max_sector_exposure_pct NUMERIC(5,2) NOT NULL DEFAULT 30.00
    """)
    
    # ── live_orders slippage tracking ─────────────────────────────────────────
    op.execute("""
        ALTER TABLE live_orders
        ADD COLUMN IF NOT EXISTS expected_price NUMERIC(12,4)
    """)
    op.execute("""
        ALTER TABLE live_orders
        ADD COLUMN IF NOT EXISTS slippage_pct NUMERIC(8,4)
    """)
    
    # ── pipeline_task_status seed ─────────────────────────────────────────────
    op.execute("""
        INSERT INTO pipeline_task_status (task_name) VALUES
            ('signal_outcome_evaluation')
        ON CONFLICT (task_name) DO NOTHING
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS signal_outcomes CASCADE")
    op.execute("ALTER TABLE user_settings DROP COLUMN IF EXISTS enforce_market_hours")
    op.execute("ALTER TABLE user_settings DROP COLUMN IF EXISTS max_sector_exposure_pct")
    op.execute("ALTER TABLE live_orders DROP COLUMN IF EXISTS expected_price")
    op.execute("ALTER TABLE live_orders DROP COLUMN IF EXISTS slippage_pct")
    op.execute("DELETE FROM pipeline_task_status WHERE task_name = 'signal_outcome_evaluation'")
