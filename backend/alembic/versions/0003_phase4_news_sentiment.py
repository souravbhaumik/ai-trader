"""Phase 4 — news_sentiment hypertable.

Stores per-article FinBERT sentiment scores linked to NSE symbols.
TimescaleDB converts this to a hypertable partitioned by ``published_at``.

Revision ID: 0003
Revises: 0002
Create Date: 2026-04-17
"""
from __future__ import annotations

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS news_sentiment (
            id              UUID        NOT NULL DEFAULT gen_random_uuid(),
            published_at    TIMESTAMPTZ NOT NULL,
            symbol          VARCHAR(32) NOT NULL,   -- NSE symbol, e.g. RELIANCE.NS
            headline        TEXT        NOT NULL,
            source          VARCHAR(64) NOT NULL,   -- e.g. 'et_rss', 'google_news'
            url             TEXT,
            sentiment       VARCHAR(8)  NOT NULL,   -- 'positive' | 'negative' | 'neutral'
            score           REAL        NOT NULL,   -- raw FinBERT positive-class probability
            confidence      REAL        NOT NULL,   -- max(pos, neg, neu) probability
            created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (id, published_at)          -- composite PK required by TimescaleDB
        );
    """)

    # Convert to hypertable — partition by published_at, 1-day chunks
    op.execute("""
        SELECT create_hypertable(
            'news_sentiment', 'published_at',
            chunk_time_interval => INTERVAL '1 day',
            if_not_exists       => TRUE
        );
    """)

    # Indexes for fast per-symbol queries
    # NOTE: TimescaleDB requires all unique indexes to include the partitioning
    # column (published_at). A URL-only dedup unique index is therefore not
    # possible — deduplication is handled in the Celery task instead.
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_news_sentiment_symbol_ts
            ON news_sentiment (symbol, published_at DESC);

        CREATE INDEX IF NOT EXISTS ix_news_sentiment_source
            ON news_sentiment (source, published_at DESC);

        CREATE INDEX IF NOT EXISTS ix_news_sentiment_url
            ON news_sentiment (url, published_at DESC);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS news_sentiment;")
