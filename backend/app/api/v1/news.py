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
    summary: Optional[str]
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
        # Correct 3-class polarity: use sentiment label, not score*2-1
        # row[3]=score (positive prob), row[6]=sentiment label
        sentiment_label = row[6] if len(row) > 6 else "neutral"
        if sentiment_label == "positive":
            polarity = row[3]    # e.g. 0.85
        elif sentiment_label == "negative":
            polarity = -row[3]   # e.g. -0.80
        else:
            polarity = 0.0       # neutral → no directional contribution
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
        logger.warning("news.sentiment.cache_read_failed", err=str(exc))

    # ── 2. DB fallback ────────────────────────────────────────────────────────
    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=24)
    rows = (await session.execute(
        text("""
            SELECT symbol, headline, source, score, confidence, published_at, sentiment
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
            SELECT id, symbol, headline, summary, source, url,
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
            summary=row[3],
            source=row[4],
            url=row[5],
            sentiment=row[6],
            score=float(row[7]),
            confidence=float(row[8]),
            published_at=row[9].isoformat() if hasattr(row[9], "isoformat") else str(row[9]),
        )
        for row in rows
    ]


class LiveAnalysisResponse(BaseModel):
    symbol: str
    score: float
    article_count: int
    articles: list[NewsArticle]


@router.post("/live-analysis", response_model=LiveAnalysisResponse)
async def live_analysis(
    symbol: str = Query(..., description="NSE symbol, e.g. RELIANCE"),
    _user=Depends(get_current_user),
):
    """On-demand live news fetch + FinBERT scoring for a symbol.

    Fetches fresh news from Google News and Yahoo Finance right now,
    scores with FinBERT, and returns the results without persisting.
    """
    import asyncio
    from app.services.news_fetcher import fetch_google_news, fetch_yahoo_finance_news
    from app.services.ner_mapper import map_headline_to_symbols
    from app.services.sentiment_scorer import score_headlines

    sym = symbol.upper().strip()

    loop = asyncio.get_running_loop()

    # Fetch in background thread (blocking I/O)
    def _fetch():
        gn = fetch_google_news([sym], max_per_symbol=5)
        yf = fetch_yahoo_finance_news([f"{sym}.NS"], max_per_ticker=5)
        return gn + yf

    articles = await loop.run_in_executor(None, _fetch)

    if not articles:
        raise HTTPException(status_code=404, detail=f"No fresh news found for {sym}")

    # Score all
    texts = [
        (a["title"] + ". " + (a.get("summary") or "")).strip()
        for a in articles
    ]
    scores = await loop.run_in_executor(None, score_headlines, texts)

    result_articles = []
    import uuid as _uuid
    for article, sr in zip(articles, scores):
        result_articles.append(NewsArticle(
            id=str(_uuid.uuid4()),
            symbol=sym,
            headline=article["title"],
            summary=article.get("summary"),
            source=article["source"],
            url=article.get("url"),
            sentiment=sr.sentiment,
            score=sr.score,
            confidence=sr.confidence,
            published_at=article["published"].isoformat()
            if hasattr(article["published"], "isoformat")
            else str(article["published"]),
        ))

    # Aggregate score
    total_w = 0.0
    weighted = 0.0
    for sr in scores:
        w = sr.confidence
        # Correct 3-class polarity: P(positive) - P(negative)
        weighted += (sr.score - sr.neg_score) * w
        total_w += w
    agg = round(weighted / total_w, 4) if total_w else 0.0

    return LiveAnalysisResponse(
        symbol=sym,
        score=agg,
        article_count=len(result_articles),
        articles=result_articles,
    )
