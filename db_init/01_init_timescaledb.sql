-- ============================================================
-- AI Trader — Database Initialization
-- Single script; runs once when the Docker postgres container
-- first starts.  All table schemas live in Alembic migrations
-- (backend/alembic/versions/).  This file only covers:
--   1. PostgreSQL extensions required before Alembic runs
--   2. The shared update-timestamp trigger function
--   3. Tables that live OUTSIDE Alembic (infra-level, not ORM)
-- ============================================================

-- ── Extensions ────────────────────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid() on PG < 13

-- ── Shared trigger function ───────────────────────────────────────────────────
-- Keeps tbl_last_dt current on every UPDATE.  Attached to each application
-- table by the Alembic migration that creates it.
CREATE OR REPLACE FUNCTION fn_set_tbl_last_dt()
RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
    NEW.tbl_last_dt = NOW();
    RETURN NEW;
END;
$$;

-- ── pipeline_task_status ──────────────────────────────────────────────────────
-- One row per background task; upserted on every status transition.
-- Stored in the DB (not Redis) so stale "running" states are cleaned up
-- automatically on worker crash / restart.
-- NOT managed by Alembic because it is infrastructure, not application schema.
CREATE TABLE IF NOT EXISTS pipeline_task_status (
    task_name    VARCHAR(100)  PRIMARY KEY,
    status       VARCHAR(20)   NOT NULL DEFAULT 'idle',
    message      TEXT          NOT NULL DEFAULT 'Never run.',
    started_at   TIMESTAMPTZ,
    finished_at  TIMESTAMPTZ,
    summary      JSONB         NOT NULL DEFAULT '{}',
    updated_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

-- Seed known tasks as idle so the Admin panel always has rows for every task.
INSERT INTO pipeline_task_status (task_name) VALUES
    ('universe_population'),
    ('broker_backfill'),
    ('bhavcopy'),
    ('backfill'),
    ('feature_engineering'),
    ('eod_ingest'),
    ('ml_training'),
    ('signal_generator'),
    ('news_sentiment'),
    ('broker_reconnect')
ON CONFLICT (task_name) DO NOTHING;
