"""News sentiment API — Phase 4.

Endpoints
---------
GET /api/v1/news/sentiment?symbol=RELIANCE
    Returns the aggregated rolling 24-h sentiment score for a symbol from
    the Redis cache, with a DB fallback if the cache is cold.

GET /api/v1/news/feed?symbol=RELIANCE&limit=20
    Returns the most recent individual headlines (with per-article sentiment)
    for a symbol from the news_sentiment table.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, Query, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from app.api.v1.deps import get_current_user, get_redis
from app.core.database import get_async_session

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/news", tags=["news"])

_SENTIMENT_KEY_PREFIX = "sentiment:"


# ── Response models ────────────────────────────────────────────────────────────

class AggregatedSentiment(BaseModel):
    symbol: str
    score: float           # [-1, 1]
    article_count: int
    last_updated: Optional[str] = None
    source: str            # "cache" | "db"


class NewsArticle(BaseModel):
    id: str
    symbol: str
    headline: str
    source: str
    url: Optional[str]
    sentiment: str         # "positive" | "neutral" | "negative"
    score: float           # [0, 1] FinBERT positive probability
    confidence: float
    published_at: str


# ── Helpers ────────────────────────────────────────────────────────────────────

def _score_from_db(rows) -> AggregatedSentiment | None:
    """Compute weighted average score from DB rows (fallback)."""
    if not rows:
        return None

    import math
    now_utc = datetime.now(tz=timezone.utc)
    total_weight = 0.0
    weighted_sum = 0.0
    count = 0

    for row in rows:
        pub = row[5]  # published_at
        if isinstance(pub, str):
            pub = datetime.fromisoformat(pub)
        if pub.tzinfo is None:
            pub = pub.replace(tzinfo=timezone.utc)
        age_hours = max((now_utc - pub).total_seconds() / 3600, 0)
        decay     = math.exp(-age_hours / 12)
        weight    = row[4] * decay   # confidence * decay
        polarity  = row[3] * 2 - 1  # score [0,1] → [-1,1]
        weighted_sum  += polarity * weight
        total_weight  += weight
        count += 1

    agg = round(weighted_sum / total_weight, 4) if total_weight else 0.0
    return AggregatedSentiment(
        symbol=rows[0][0],
        score=agg,
        article_count=count,
        last_updated=now_utc.isoformat(),
        source="db",
    )


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.get("/sentiment", response_model=AggregatedSentiment)
async def get_sentiment(
    symbol: str = Query(..., description="NSE symbol, e.g. RELIANCE"),
    _user=Depends(get_current_user),
    redis=Depends(get_redis),
    session=Depends(get_async_session),
):
    """Return the aggregated rolling 24-h sentiment score for a symbol."""
    sym = symbol.upper().strip()

    # ── 1. Try Redis cache ────────────────────────────────────────────────────
    try:
        cached = await redis.get(f"{_SENTIMENT_KEY_PREFIX}{sym}")
        if cached:
            data = json.loads(cached)
            return AggregatedSentiment(
                symbol=data["symbol"],
                score=data["score"],
                article_count=data["article_count"],
                last_updated=data.get("last_updated"),
                source="cache",
            )
    except Exception as exc:
        logger.warning("news.sentiment.cache_read_failed", error=str(exc))

    # ── 2. DB fallback ────────────────────────────────────────────────────────
    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=24)
    rows = (await session.execute(
        text("""
            SELECT symbol, headline, source, score, confidence, published_at
            FROM   news_sentiment
            WHERE  symbol = :sym AND published_at >= :cutoff
            ORDER  BY published_at DESC
        """),
        {"sym": sym, "cutoff": cutoff},
    )).fetchall()

    result = _score_from_db(rows)
    if result is None:
        raise HTTPException(status_code=404, detail=f"No sentiment data for {sym}")
    return result


@router.get("/feed", response_model=list[NewsArticle])
async def get_feed(
    symbol: str = Query(..., description="NSE symbol, e.g. RELIANCE"),
    limit: int = Query(20, ge=1, le=100),
    _user=Depends(get_current_user),
    session=Depends(get_async_session),
):
    """Return recent news articles with sentiment for a symbol."""
    sym = symbol.upper().strip()
    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=72)

    rows = (await session.execute(
        text("""
            SELECT id, symbol, headline, source, url,
                   sentiment, score, confidence, published_at
            FROM   news_sentiment
            WHERE  symbol = :sym AND published_at >= :cutoff
            ORDER  BY published_at DESC
            LIMIT  :lim
        """),
        {"sym": sym, "cutoff": cutoff, "lim": limit},
    )).fetchall()

    return [
        NewsArticle(
            id=str(row[0]),
            symbol=row[1],
            headline=row[2],
            source=row[3],
            url=row[4],
            sentiment=row[5],
            score=float(row[6]),
            confidence=float(row[7]),
            published_at=row[8].isoformat() if hasattr(row[8], "isoformat") else str(row[8]),
        )
        for row in rows
    ]
