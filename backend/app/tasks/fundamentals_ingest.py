"""Fundamentals ingestion task — Phase 3b.

Runs daily at 7:00 AM IST (Mon–Fri) before the pre-market signal generation
at 8:30 AM.  Fetches fundamental data for all active stock universe symbols
from yfinance and caches the result in Redis.

Since yfinance enforces aggressive rate limits on large batches, we process
symbols in small batches with a short sleep between requests.  A single run
over the full 500-symbol universe takes roughly 8–12 minutes — well within
the 90-minute pre-market window.

Freshness policy
----------------
- 24h TTL on each Redis key (``fundamentals:<SYMBOL>``)
- Symbols already cached and fresh (< 20h old) are skipped to reduce
  yfinance load on re-runs
- If yfinance returns insufficient data (quality gate in fundamentals_service),
  the symbol is silently skipped; no stale partial data is written
"""
from __future__ import annotations

import time
from datetime import datetime, timezone

import structlog

from app.tasks.celery_app import celery_app

logger = structlog.get_logger(__name__)

_TASK          = "fundamentals_ingest"
_BATCH_SIZE    = 10    # symbols per mini-batch
_SLEEP_BETWEEN = 2.0   # seconds between batches (yfinance rate limiting)
_FRESHNESS_SEC = 72_000  # 20h — skip symbols cached more recently than this


@celery_app.task(
    name="app.tasks.fundamentals_ingest.refresh_fundamentals",
    bind=True,
    max_retries=2,
    default_retry_delay=300,
    autoretry_for=(Exception,),
)
def refresh_fundamentals(self) -> dict:
    """Fetch and cache fundamentals for all active stock universe symbols."""
    import json
    import redis as _redis
    from sqlalchemy import text
    from app.core.config import settings
    from app.core.database import get_sync_session
    from app.services.fundamentals_service import fetch_and_cache, get_fundamentals_from_cache

    started_at = datetime.now(tz=timezone.utc)
    logger.info("fundamentals_ingest.started")

    r = _redis.from_url(settings.redis_url, decode_responses=True)

    # ── Load active symbols ────────────────────────────────────────────────────
    try:
        with get_sync_session() as session:
            rows = session.execute(
                text(
                    "SELECT symbol FROM stock_universe"
                    " WHERE is_active = TRUE"
                    " ORDER BY market_cap DESC NULLS LAST"
                )
            ).fetchall()
        symbols = [r_[0] for r_ in rows]
    except Exception as exc:
        logger.error("fundamentals_ingest.db_load_failed", err=str(exc))
        raise

    total    = len(symbols)
    fetched  = 0
    skipped  = 0
    failed   = 0

    logger.info("fundamentals_ingest.symbols_loaded", total=total)

    # ── Process in batches ─────────────────────────────────────────────────────
    for i in range(0, total, _BATCH_SIZE):
        batch = symbols[i : i + _BATCH_SIZE]

        for sym in batch:
            # Skip if already fresh in cache
            try:
                cached = get_fundamentals_from_cache(sym, redis_client=r)
                if cached:
                    fetched_at_str = cached.get("fetched_at")
                    if fetched_at_str:
                        fetched_at = datetime.fromisoformat(fetched_at_str)
                        if fetched_at.tzinfo is None:
                            fetched_at = fetched_at.replace(tzinfo=timezone.utc)
                        age_secs = (datetime.now(tz=timezone.utc) - fetched_at).total_seconds()
                        if age_secs < _FRESHNESS_SEC:
                            skipped += 1
                            continue
            except Exception:
                pass  # If cache check fails, proceed with fresh fetch

            # Fetch from yfinance + write to Redis
            try:
                data = fetch_and_cache(sym, redis_client=r)
                if data is not None:
                    fetched += 1
                else:
                    failed += 1   # quality gate filtered it
            except Exception as exc:
                logger.warning("fundamentals_ingest.symbol_failed", symbol=sym, err=str(exc))
                failed += 1

        # Rate-limit sleep between batches
        if i + _BATCH_SIZE < total:
            time.sleep(_SLEEP_BETWEEN)

    elapsed = round((datetime.now(tz=timezone.utc) - started_at).total_seconds(), 1)
    logger.info(
        "fundamentals_ingest.done",
        total=total,
        fetched=fetched,
        skipped=skipped,
        failed=failed,
        elapsed_seconds=elapsed,
    )
    return {
        "status":  "done",
        "total":   total,
        "fetched": fetched,
        "skipped": skipped,
        "failed":  failed,
        "elapsed": elapsed,
    }
