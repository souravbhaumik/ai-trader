"""Add tbl_last_dt + triggers to tables that were missing audit columns.

- ohlcv_daily / ohlcv_1min: add created_at only (TimescaleDB compression blocks
  UPDATE triggers on compressed chunks — tbl_last_dt would silently fail there).
- signals, ml_models, paper_trades, live_orders: add tbl_last_dt + trigger.

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-20
"""
from __future__ import annotations

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── ohlcv_daily ───────────────────────────────────────────────────────────
    # TimescaleDB hypertable with compression (columnstore) — cannot use
    # non-constant DEFAULT NOW() in ALTER TABLE ADD COLUMN directly.
    # Two-step: add nullable, then set default for future inserts.
    op.execute("""
        ALTER TABLE ohlcv_daily
            ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ
    """)
    op.execute("""
        ALTER TABLE ohlcv_daily
            ALTER COLUMN created_at SET DEFAULT NOW()
    """)

    # ── ohlcv_1min ────────────────────────────────────────────────────────────
    op.execute("""
        ALTER TABLE ohlcv_1min
            ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ
    """)
    op.execute("""
        ALTER TABLE ohlcv_1min
            ALTER COLUMN created_at SET DEFAULT NOW()
    """)

    # ── signals ───────────────────────────────────────────────────────────────
    # Hypertable but no compression policy → UPDATE trigger is safe.
    op.execute("""
        ALTER TABLE signals
            ADD COLUMN IF NOT EXISTS tbl_last_dt TIMESTAMPTZ NOT NULL DEFAULT NOW()
    """)
    op.execute("""
        CREATE TRIGGER trg_signals_tbl_last_dt
        BEFORE UPDATE ON signals
        FOR EACH ROW EXECUTE FUNCTION fn_set_tbl_last_dt()
    """)

    # ── ml_models ─────────────────────────────────────────────────────────────
    op.execute("""
        ALTER TABLE ml_models
            ADD COLUMN IF NOT EXISTS tbl_last_dt TIMESTAMPTZ NOT NULL DEFAULT NOW()
    """)
    op.execute("""
        CREATE TRIGGER trg_ml_models_tbl_last_dt
        BEFORE UPDATE ON ml_models
        FOR EACH ROW EXECUTE FUNCTION fn_set_tbl_last_dt()
    """)

    # ── paper_trades ──────────────────────────────────────────────────────────
    op.execute("""
        ALTER TABLE paper_trades
            ADD COLUMN IF NOT EXISTS tbl_last_dt TIMESTAMPTZ NOT NULL DEFAULT NOW()
    """)
    op.execute("""
        CREATE TRIGGER trg_paper_trades_tbl_last_dt
        BEFORE UPDATE ON paper_trades
        FOR EACH ROW EXECUTE FUNCTION fn_set_tbl_last_dt()
    """)

    # ── live_orders ───────────────────────────────────────────────────────────
    # Already has updated_at (manually set in code); tbl_last_dt gives a
    # DB-level guarantee that is trigger-driven rather than app-driven.
    op.execute("""
        ALTER TABLE live_orders
            ADD COLUMN IF NOT EXISTS tbl_last_dt TIMESTAMPTZ NOT NULL DEFAULT NOW()
    """)
    op.execute("""
        CREATE TRIGGER trg_live_orders_tbl_last_dt
        BEFORE UPDATE ON live_orders
        FOR EACH ROW EXECUTE FUNCTION fn_set_tbl_last_dt()
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_live_orders_tbl_last_dt   ON live_orders")
    op.execute("DROP TRIGGER IF EXISTS trg_paper_trades_tbl_last_dt  ON paper_trades")
    op.execute("DROP TRIGGER IF EXISTS trg_ml_models_tbl_last_dt     ON ml_models")
    op.execute("DROP TRIGGER IF EXISTS trg_signals_tbl_last_dt       ON signals")
    op.execute("ALTER TABLE live_orders   DROP COLUMN IF EXISTS tbl_last_dt")
    op.execute("ALTER TABLE paper_trades  DROP COLUMN IF EXISTS tbl_last_dt")
    op.execute("ALTER TABLE ml_models     DROP COLUMN IF EXISTS tbl_last_dt")
    op.execute("ALTER TABLE signals       DROP COLUMN IF EXISTS tbl_last_dt")
    op.execute("ALTER TABLE ohlcv_1min    DROP COLUMN IF EXISTS created_at")
    op.execute("ALTER TABLE ohlcv_daily   DROP COLUMN IF EXISTS created_at")
