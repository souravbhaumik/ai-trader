"""add dedup_hash to news_sentiment

Revision ID: 0004
Revises: 0003_add_signal_explanation
Create Date: 2026-04-20
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "news_sentiment",
        sa.Column("dedup_hash", sa.String(64), nullable=True),
    )
    # TimescaleDB hypertable partitioned by published_at — unique index must
    # include the partition column. Use a plain index instead of a constraint.
    op.create_index(
        "uq_news_sentiment_dedup_hash",
        "news_sentiment",
        ["dedup_hash", "published_at"],
        unique=True,
    )
    # Add broker_order_id index for live_orders (Phase 7 ISSUE-06)
    op.create_index(
        "idx_live_orders_broker_oid",
        "live_orders",
        ["broker_order_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_live_orders_broker_oid", table_name="live_orders")
    op.drop_index("uq_news_sentiment_dedup_hash", table_name="news_sentiment")
    op.drop_column("news_sentiment", "dedup_hash")
