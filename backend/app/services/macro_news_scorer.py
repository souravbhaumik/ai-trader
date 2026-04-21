"""Global macro news sentiment scorer.

Scores a batch of global/geopolitical headlines with FinBERT and produces a
single aggregated ``macro_news_score`` (range -1 to +1) that is written to
Redis under ``macro:news:score`` (TTL 2h).

This score is consumed by the regime detector to adjust the market regime
determination beyond pure price proxies (VIX, Nifty 20d return, etc.).

A strongly negative macro score from Reuters/AP/Google News headlines about
events like "Trump tariffs", "Russia-Ukraine sanctions", or "Fed rate hike
shock" will push the regime toward ``risk_off`` even before VIX reacts.

Redis keys
----------
``macro:news:score``    float string, range [-1, 1]
``macro:news:meta``     JSON: score, article_count, top_topics, last_updated
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)

_MACRO_SCORE_KEY = "macro:news:score"
_MACRO_META_KEY  = "macro:news:meta"
_MACRO_TTL_SECS  = 7200   # 2 hours — same as sentiment cache


# ── Keyword topic tagger ───────────────────────────────────────────────────────
# Used to annotate the ``top_topics`` field in the meta payload; no scoring
# impact — purely informational for the UI.
_TOPIC_KEYWORDS: dict[str, list[str]] = {
    "rate_policy":    ["federal reserve", "fed rate", "rbi rate", "interest rate", "monetary policy", "rate hike", "rate cut"],
    "geopolitical":   ["russia", "ukraine", "war", "sanction", "trump tariff", "tariff", "trade war", "china india"],
    "energy":         ["crude oil", "brent", "opec", "oil price", "natural gas"],
    "macro_india":    ["india gdp", "india inflation", "india cpi", "india growth", "rbi", "nifty", "sensex"],
    "fx_carry":       ["dollar rupee", "usdinr", "rupee", "dollar index", "dxy"],
    "recession_risk": ["recession", "slowdown", "gdp contraction", "downturn", "stagflation"],
    "fii_flow":       ["fii", "fpi", "foreign inflow", "foreign outflow", "institutional selling"],
}


def _tag_topics(texts: list[str]) -> list[str]:
    """Return list of detected topics from a batch of headlines."""
    combined = " ".join(texts).lower()
    found = [topic for topic, kws in _TOPIC_KEYWORDS.items() if any(kw in combined for kw in kws)]
    return found


def score_macro_headlines(headlines: list[str]) -> float:
    """Score a list of global macro headlines and write result to Redis.

    Uses the shared FinBERT pipeline (already loaded in the Celery worker
    process for equity news scoring — no extra memory cost).

    The aggregated score is a simple arithmetic mean of per-headline
    polarity values (positive → +1, negative → -1, neutral → 0), weighted
    by FinBERT confidence for that headline.

    Args:
        headlines: list of headline strings (no symbol context needed)

    Returns:
        Aggregated macro score in [-1, 1]. 0.0 on any failure.
    """
    if not headlines:
        return 0.0

    try:
        from app.services.sentiment_scorer import score_headlines
        results = score_headlines(headlines)

        weighted_sum  = 0.0
        total_weight  = 0.0
        for result in results:
            polarity = result.score * 2 - 1          # [0,1] → [-1,1]
            weight   = result.confidence
            weighted_sum  += polarity * weight
            total_weight  += weight

        agg_score = round(weighted_sum / total_weight, 4) if total_weight else 0.0

        # Clamp to [-1, 1]
        agg_score = max(-1.0, min(1.0, agg_score))

        topics = _tag_topics(headlines)
        now_str = datetime.now(tz=timezone.utc).isoformat()

        _write_to_redis(agg_score, len(headlines), topics, now_str)

        logger.info(
            "macro_news_scorer.scored",
            score=agg_score,
            article_count=len(headlines),
            topics=topics,
        )
        return agg_score

    except Exception as exc:
        logger.error("macro_news_scorer.failed", err=str(exc))
        return 0.0


def _write_to_redis(score: float, article_count: int, topics: list[str], last_updated: str) -> None:
    """Persist macro news score and metadata to Redis."""
    try:
        import redis as _redis
        from app.core.config import settings

        r = _redis.from_url(settings.redis_url, decode_responses=True)
        r.setex(_MACRO_SCORE_KEY, _MACRO_TTL_SECS, str(score))
        r.setex(
            _MACRO_META_KEY,
            _MACRO_TTL_SECS,
            json.dumps({
                "score":         score,
                "article_count": article_count,
                "top_topics":    topics,
                "last_updated":  last_updated,
            }),
        )
    except Exception as exc:
        logger.warning("macro_news_scorer.redis_write_failed", err=str(exc))


def get_macro_news_score(redis_client=None) -> float:
    """Read the latest macro news score from Redis.

    Args:
        redis_client: optional pre-created Redis client. If None, creates one.

    Returns:
        Float in [-1, 1]. Returns 0.0 (neutral) if key is absent or on error.
    """
    try:
        if redis_client is None:
            import redis as _redis
            from app.core.config import settings
            redis_client = _redis.from_url(settings.redis_url, decode_responses=True)

        raw = redis_client.get(_MACRO_SCORE_KEY)
        if raw is None:
            return 0.0
        return float(raw)
    except Exception as exc:
        logger.warning("macro_news_scorer.read_failed", err=str(exc))
        return 0.0


def get_macro_news_meta(redis_client=None) -> Optional[dict]:
    """Read full macro news metadata from Redis (score + topics + timestamp)."""
    try:
        if redis_client is None:
            import redis as _redis
            from app.core.config import settings
            redis_client = _redis.from_url(settings.redis_url, decode_responses=True)

        raw = redis_client.get(_MACRO_META_KEY)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception as exc:
        logger.warning("macro_news_scorer.meta_read_failed", err=str(exc))
        return None
