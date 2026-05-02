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
import hashlib
import math
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

import structlog
from sqlalchemy import text

from app.core.database import get_sync_session
from app.tasks.celery_app import celery_app

# IST timezone constant — all market-context timestamps use IST
_IST = timezone(timedelta(hours=5, minutes=30))

logger = structlog.get_logger(__name__)

_SENTIMENT_KEY_PREFIX = "sentiment:"
_SENTIMENT_TTL_SECS   = 50400  # 14 hours — survives overnight; 4:45 PM → still valid at 8:30 AM
_ROLLING_WINDOW_HOURS = 24
_DEDUP_KEY            = "news:dedup:seen"  # Redis SET of SHA256 hashes
_DEDUP_TTL            = 86400              # 24h — matches rolling sentiment window


@celery_app.task(
    name="app.tasks.news_sentiment.fetch_news_sentiment",
    bind=True,
    max_retries=3,
    default_retry_delay=120,      # 2-minute back-off between retries
    autoretry_for=(Exception,),   # retry on any uncaught exception
    retry_backoff=True,           # exponential back-off: 120s, 240s, 480s
    retry_backoff_max=600,
    retry_jitter=True,
)
def fetch_news_sentiment(self):
    """Fetch, NER-map, score, persist, and cache news sentiment."""
    from app.services.news_fetcher   import fetch_rss, fetch_google_news, fetch_yahoo_finance_news
    from app.services.ner_mapper     import map_headline_to_symbols, _ensure_universe
    from app.services.sentiment_scorer import score_headlines

    # ── 1. Ensure NER universe is warm ────────────────────────────────────────
    try:
        _ensure_universe()
    except Exception as exc:
        logger.warning("news_task.ner_universe_warm_failed", err=str(exc))
        # Non-fatal: NER will still work with whatever universe is cached

    # ── 2. Fetch headlines ────────────────────────────────────────────────────
    # RSS feeds run per-feed with isolation — one broken feed never stops others
    rss_articles = fetch_rss()

    # Google News + Yahoo Finance — wrapped independently
    gn_articles: list[dict] = []
    yf_articles: list[dict] = []
    macro_articles: list[dict] = []
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
    except Exception as exc:
        logger.warning("news_task.db_top_names_failed", err=str(exc))
        top_names = []
        top_queries = []

    try:
        gn_articles = fetch_google_news(top_queries, max_per_symbol=3)
    except Exception as exc:
        logger.warning("news_task.google_news_failed", err=str(exc))

    try:
        yf_articles = fetch_yahoo_finance_news(top_names, max_per_ticker=5)
    except Exception as exc:
        logger.warning("news_task.yahoo_news_failed", err=str(exc))

    try:
        from app.services.news_fetcher import fetch_macro_news
        macro_articles = fetch_macro_news()
    except Exception as exc:
        logger.warning("news_task.macro_news_failed", err=str(exc))

    all_articles: list[dict] = rss_articles + gn_articles + yf_articles + macro_articles
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

    # ── 4. Deduplication BEFORE FinBERT inference ─────────────────────────────
    # Compute a dedup key per article: prefer URL, fall back to MD5 of headline.
    # Articles already in the DB (last 24 h) are skipped so we never re-score.
    def _dedup_key(article: dict) -> str:
        if article.get("url"):
            return article["url"]
        return "hl:" + hashlib.md5(article["title"].encode("utf-8", errors="replace")).hexdigest()

    cutoff_pre = datetime.now(tz=timezone.utc) - timedelta(hours=24)
    try:
        with get_sync_session() as session:
            existing_urls: set[str] = {
                row[0] for row in session.execute(
                    text(
                        "SELECT url FROM news_sentiment"
                        " WHERE published_at >= :cutoff AND url IS NOT NULL"
                    ),
                    {"cutoff": cutoff_pre},
                ).fetchall()
            }
            existing_headlines: set[str] = {
                "hl:" + hashlib.md5(row[0].encode("utf-8", errors="replace")).hexdigest()
                for row in session.execute(
                    text(
                        "SELECT headline FROM news_sentiment"
                        " WHERE published_at >= :cutoff"
                    ),
                    {"cutoff": cutoff_pre},
                ).fetchall()
            }
        seen_keys = existing_urls | existing_headlines
    except Exception as exc:
        logger.warning("news_task.dedup_lookup_failed", err=str(exc))
        seen_keys = set()

    new_mapped: list[dict] = []
    for article in mapped:
        key = _dedup_key(article)
        if key not in seen_keys:
            new_mapped.append(article)
            seen_keys.add(key)   # prevent duplicates within this batch too

    logger.info(
        "news_task.dedup",
        total_mapped=len(mapped),
        new_articles=len(new_mapped),
        skipped=len(mapped) - len(new_mapped),
    )
    mapped = new_mapped

    if not mapped:
        logger.info("news_task.all_duplicates")
        return {"status": "done", "inserted": 0}

    # ── 5. Batch FinBERT scoring ──────────────────────────────────────────────
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

    # ── 6. Persist to DB (batch INSERT with dedup_hash) ─────────────────────
    # Compute dedup_hash for each row for DB-side duplicate prevention
    for row in rows:
        raw_key = (row.get("url") or row["headline"]) + "||" + row["symbol"]
        row["dedup_hash"] = hashlib.sha256(raw_key.encode("utf-8", errors="replace")).hexdigest()

    inserted = 0
    try:
        with get_sync_session() as session:
            # Batch insert — ON CONFLICT skips duplicates at DB level
            for i in range(0, len(rows), 100):
                batch = rows[i : i + 100]
                try:
                    result = session.execute(
                        text("""
                            INSERT INTO news_sentiment
                                (id, published_at, symbol, headline, summary, source,
                                 url, sentiment, score, confidence, created_at, dedup_hash)
                            VALUES
                                (:id, :published_at, :symbol, :headline, :summary, :source,
                                 :url, :sentiment, :score, :confidence, :created_at, :dedup_hash)
                            ON CONFLICT (dedup_hash) DO NOTHING
                        """),
                        batch,
                    )
                    inserted += result.rowcount
                except Exception as exc:
                    logger.warning("news_task.batch_insert_failed", err=str(exc), batch_size=len(batch))
            session.commit()
    except Exception as exc:
        logger.error("news_task.db_insert_failed", err=str(exc))

    # ── 7. Recompute rolling sentiment cache in Redis ─────────────────────────
    _update_sentiment_cache(rows, now_utc)

    # ── 8. Update global macro news score (for regime detector) ───────────────
    try:
        from app.services.macro_news_scorer import score_macro_headlines
        all_texts = [r["headline"] for r in rows if r.get("headline")]
        if all_texts:
            score_macro_headlines(all_texts)
    except Exception as exc:
        logger.warning("news_task.macro_score_failed", err=str(exc))

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
            logger.warning("news_task.cache_db_query_failed", err=str(exc))

        for sym, sym_rows in by_symbol.items():
            total_weight = 0.0
            weighted_sum = 0.0
            for row in sym_rows:
                pub = row.get("published_at", now_utc)
                if isinstance(pub, str):
                    pub = datetime.fromisoformat(pub)
                if pub.tzinfo is None:
                    pub = pub.replace(tzinfo=timezone.utc)
                else:
                    pub = pub.astimezone(timezone.utc)
                age_hours = max((now_utc - pub).total_seconds() / 3600, 0)
                decay     = math.exp(-age_hours / 12)     # half-life ~12 hours
                weight    = row["confidence"] * decay
                # Correct 3-class polarity using sentiment label.
                # The old `score * 2 - 1` formula was biased bearish for
                # neutral articles (P≈0.33 → polarity ≈ −0.34).
                # DB rows store label + positive score; derive sign from label.
                sentiment_label = row.get("sentiment", "neutral")
                if sentiment_label == "positive":
                    polarity = row["score"]    # e.g. 0.87
                elif sentiment_label == "negative":
                    polarity = -row["score"]   # e.g. −0.82
                else:
                    polarity = 0.0             # neutral → no contribution
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
        logger.error("news_task.cache_update_failed", err=str(exc))
