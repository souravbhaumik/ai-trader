-- ============================================================
-- AI Trader — TimescaleDB Initialization
-- Runs once when the Docker postgres container first starts.
-- Relational tables are managed by Alembic migrations.
-- ============================================================

-- Enable TimescaleDB
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- Enable pgcrypto for gen_random_uuid() on PG < 13
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ── Trigger function: keep tbl_last_dt current on every UPDATE ───────────────
CREATE OR REPLACE FUNCTION fn_set_tbl_last_dt()
RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
    NEW.tbl_last_dt = NOW();
    RETURN NEW;
END;
$$;

-- ── Note ─────────────────────────────────────────────────────────────────────
-- TimescaleDB hypertables (ohlcv_daily, ohlcv_1min, signals, etc.) will be
-- created in Phase 2 via a dedicated migration file.
-- The trigger fn_set_tbl_last_dt() will be attached to each new table by
-- the Alembic migration that creates it.
