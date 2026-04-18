-- ============================================================
-- Pipeline task status table
-- One row per task, upserted on every status transition.
-- Replaces the Redis pipeline:status:* keys, which are prone
-- to stale "running" states after worker crashes.
-- ============================================================

CREATE TABLE IF NOT EXISTS pipeline_task_status (
    task_name    VARCHAR(100)  PRIMARY KEY,
    status       VARCHAR(20)   NOT NULL DEFAULT 'idle',
    message      TEXT          NOT NULL DEFAULT 'Never run.',
    started_at   TIMESTAMPTZ,
    finished_at  TIMESTAMPTZ,
    summary      JSONB         NOT NULL DEFAULT '{}',
    updated_at   TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

-- Seed known tasks as idle so the Admin panel always has rows for all tasks.
INSERT INTO pipeline_task_status (task_name) VALUES
    ('universe_population'),
    ('broker_backfill'),
    ('bhavcopy'),
    ('backfill'),
    ('eod_ingest'),
    ('ml_training'),
    ('signal_generator'),
    ('news_sentiment')
ON CONFLICT (task_name) DO NOTHING;
