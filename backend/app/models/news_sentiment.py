"""NewsSentiment ORM model — maps to the ``news_sentiment`` hypertable."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


class NewsSentiment(SQLModel, table=True):
    __tablename__ = "news_sentiment"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True, nullable=False)
    symbol: str = Field(max_length=32, index=True)
    headline: str = Field(max_length=2000)
    summary: Optional[str] = Field(default=None, max_length=1000)
    source: str = Field(max_length=50)
    url: Optional[str] = Field(default=None, max_length=2048)
    sentiment: str = Field(max_length=10)  # positive / negative / neutral
    score: float = Field(default=0.0)      # positive-class probability [0,1]
    confidence: float = Field(default=0.0) # max-class probability [0,1]
    published_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )
    dedup_hash: Optional[str] = Field(default=None, max_length=64)
