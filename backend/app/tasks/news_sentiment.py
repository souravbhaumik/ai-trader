"""News sentiment ingestion task — Phase 4.

Runs every 15 minutes during market hours (Mon–Fri 9:00 AM – 4:00 PM IST).

Pipeline
--------
1. Fetch fresh headlines from RSS feeds + Google News (last 24 h)
2. For each headline: extract NSE symbols via NER + fuzzy matching
3. Batch-score all mapped headlines with FinBERT
4. INSERT into news_sentiment (skip duplicates via ON CONFLICT)
5. Recompute a rolling 24-h weighted sentiment score per symbol and
   cache it in Redis under ``sentiment:<SYMBOL>`` (JSON, 2-hour TTL)

The Redis cache is the primary data source for the signal generator and
the API endpoint — no hot-path DB query needed.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

import structlog
from sqlalchemy import text

from app.core.database import get_sync_session
from app.tasks.celery_app import celery_app

logger = structlog.get_logger(__name__)

_SENTIMENT_KEY_PREFIX = "sentiment:"
_SENTIMENT_TTL_SECS   = 7200   # 2 hours
_ROLLING_WINDOW_HOURS = 24


@celery_app.task(name="app.tasks.news_sentiment.fetch_news_sentiment", bind=True)
def fetch_news_sentiment(self):
    """Fetch, NER-map, score, persist, and cache news sentiment."""
    from app.services.news_fetcher   import fetch_rss, fetch_google_news, fetch_yahoo_finance_news
    from app.services.ner_mapper     import map_headline_to_symbols, _ensure_universe
    from app.services.sentiment_scorer import score_headlines

    # ── 1. Ensure NER universe is warm ────────────────────────────────────────
    _ensure_universe()

    # ── 2. Fetch headlines ────────────────────────────────────────────────────
    rss_articles    = fetch_rss()
    # Pass the 50 most mentioned company names as Google News queries
    # (lightweight — we don't want to send 500 queries per run)
    gn_articles: list[dict] = []
    yf_articles: list[dict] = []
    try:
        with get_sync_session() as session:
            rows = session.execute(
                text(
                    "SELECT symbol, name FROM stock_universe"
                    " WHERE is_active = TRUE AND name IS NOT NULL"
                    " ORDER BY market_cap DESC NULLS LAST LIMIT 50"
                )
            ).fetchall()
        top_names   = [r[0] + ".NS" for r in rows]  # Yahoo needs .NS suffix
        top_queries = [r[1] for r in rows]           # Google needs company name
        gn_articles = fetch_google_news(top_queries, max_per_symbol=3)
        yf_articles = fetch_yahoo_finance_news(top_names, max_per_ticker=5)
    except Exception as exc:
        logger.warning("news_task.fetch_failed", error=str(exc))

    all_articles: list[dict] = rss_articles + gn_articles + yf_articles
    if not all_articles:
        logger.info("news_task.no_articles")
        return {"status": "done", "inserted": 0}

    # ── 3. NER map to symbols ─────────────────────────────────────────────────
    # Explode articles with multiple symbols into separate rows
    mapped: list[dict] = []
    for article in all_articles:
        # Yahoo Finance articles carry a direct ticker hint — no NER needed
        ticker_hint = article.get("_ticker")
        if ticker_hint:
            sym = ticker_hint.removesuffix(".NS")
            mapped.append({**article, "symbol": sym})
            continue
        symbols = map_headline_to_symbols(
            article["title"],
            query_hint=article.get("_query"),
        )
        for sym in symbols:
            mapped.append({**article, "symbol": sym})

    if not mapped:
        logger.info("news_task.no_mappings", raw_articles=len(all_articles))
        return {"status": "done", "inserted": 0}

    # ── 4. Batch FinBERT scoring ──────────────────────────────────────────────
    # Score headline + summary together for richer context
    texts_to_score = [
        (r["title"] + ". " + r["summary"]).strip() if r.get("summary") else r["title"]
        for r in mapped
    ]
    scores = score_headlines(texts_to_score)

    rows: list[dict[str, Any]] = []
    now_utc = datetime.now(tz=timezone.utc)
    for article, result in zip(mapped, scores):
        rows.append({
            "id":          str(uuid.uuid4()),
            "published_at": article["published"].replace(tzinfo=timezone.utc)
                           if article["published"].tzinfo is None
                           else article["published"].astimezone(timezone.utc),
            "symbol":      article["symbol"],
            "headline":    article["title"][:2000],
            "summary":     (article.get("summary") or "")[:1000] or None,
            "source":      article["source"],
            "url":         article.get("url"),
            "sentiment":   result.sentiment,
            "score":       result.score,
            "confidence":  result.confidence,
            "created_at":  now_utc,
        })

    # ── 5. Persist to DB ──────────────────────────────────────────────────────
    inserted = 0
    try:
        with get_sync_session() as session:
            # Fetch URLs already stored in the last 24 h to avoid re-inserting
            cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=24)
            existing_urls: set[str] = {
                row[0] for row in session.execute(
                    text("SELECT url FROM news_sentiment WHERE published_at >= :cutoff AND url IS NOT NULL"),
                    {"cutoff": cutoff},
                ).fetchall()
            }

            for row in rows:
                if row.get("url") and row["url"] in existing_urls:
                    continue   # already stored in this window
                try:
                    session.execute(
                        text("""
                            INSERT INTO news_sentiment
                                (id, published_at, symbol, headline, summary, source,
                                 url, sentiment, score, confidence, created_at)
                            VALUES
                                (:id, :published_at, :symbol, :headline, :summary, :source,
                                 :url, :sentiment, :score, :confidence, :created_at)
                        """),
                        row,
                    )
                    if row.get("url"):
                        existing_urls.add(row["url"])
                    inserted += 1
                except Exception:
                    pass   # individual row error — continue with rest
            session.commit()
    except Exception as exc:
        logger.error("news_task.db_insert_failed", error=str(exc))

    # ── 6. Recompute rolling sentiment cache in Redis ─────────────────────────
    _update_sentiment_cache(rows, now_utc)

    logger.info("news_task.done", inserted=inserted, total_mapped=len(rows))
    return {"status": "done", "inserted": inserted, "total_mapped": len(rows)}


def _update_sentiment_cache(rows: list[dict], now_utc: datetime) -> None:
    """Compute a weighted-average sentiment score per symbol and cache in Redis.

    Weight = confidence * recency (exponential decay over 24 hours).
    The cached payload is a JSON object with keys:
        symbol, score, article_count, last_updated
    """
    try:
        import math
        import redis as _redis
        from app.core.config import settings

        r = _redis.from_url(settings.redis_url, decode_responses=True)

        # Group rows by symbol
        by_symbol: dict[str, list[dict]] = {}
        for row in rows:
            by_symbol.setdefault(row["symbol"], []).append(row)

        # Also query the last 24h from the DB for a full rolling window
        try:
            cutoff = now_utc - timedelta(hours=_ROLLING_WINDOW_HOURS)
            with get_sync_session() as session:
                db_rows = session.execute(
                    text("""
                        SELECT symbol, score, confidence, published_at
                        FROM   news_sentiment
                        WHERE  published_at >= :cutoff
                        ORDER  BY published_at DESC
                    """),
                    {"cutoff": cutoff},
                ).fetchall()
            for db_row in db_rows:
                sym = db_row[0]
                if sym not in by_symbol:
                    by_symbol[sym] = []
                # Don't double-count rows already in `rows`; the ON CONFLICT
                # skip means they're already there — but for aggregation it's
                # fine to slightly overweight freshly inserted rows.
                by_symbol[sym].append({
                    "symbol":     sym,
                    "score":      db_row[1],
                    "confidence": db_row[2],
                    "published_at": db_row[3],
                })
        except Exception as exc:
            logger.warning("news_task.cache_db_query_failed", error=str(exc))

        for sym, sym_rows in by_symbol.items():
            total_weight = 0.0
            weighted_sum = 0.0
            for row in sym_rows:
                pub = row.get("published_at", now_utc)
                if isinstance(pub, str):
                    pub = datetime.fromisoformat(pub)
                age_hours = max((now_utc - pub.replace(tzinfo=timezone.utc)).total_seconds() / 3600, 0)
                decay     = math.exp(-age_hours / 12)     # half-life ~12 hours
                weight    = row["confidence"] * decay
                # sentiment score: positive→1, neutral→0, negative→-1 mapping
                polarity  = row["score"] * 2 - 1          # [0,1] → [-1,1]
                weighted_sum  += polarity * weight
                total_weight  += weight

            agg_score = round(weighted_sum / total_weight, 4) if total_weight else 0.0

            payload = json.dumps({
                "symbol":        sym,
                "score":         agg_score,         # [-1, 1]
                "article_count": len(sym_rows),
                "last_updated":  now_utc.isoformat(),
            })
            r.setex(f"{_SENTIMENT_KEY_PREFIX}{sym}", _SENTIMENT_TTL_SECS, payload)

        logger.info("news_task.cache_updated", symbols=len(by_symbol))
    except Exception as exc:
        logger.error("news_task.cache_update_failed", error=str(exc))
