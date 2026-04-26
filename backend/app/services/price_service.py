"""Price service — thin orchestration layer between API and broker adapters.

Handles caching in Redis (60-second TTL for quotes, 5-min for indices)
so rapid API calls don't hammer the underlying data source.
Falls back to Google Finance scraping when the primary broker returns no data.
"""
from __future__ import annotations

import json
from typing import List, Optional

import structlog

from app.brokers.base import BrokerAdapter, OHLCVBar, Quote
from app.brokers.google_finance_adapter import GoogleFinanceAdapter
from app.core.redis_client import get_redis

_gf_adapter = GoogleFinanceAdapter()

logger = structlog.get_logger(__name__)

_QUOTE_TTL    = 60   # seconds
_INDEX_TTL    = 60   # seconds
_HISTORY_TTL  = 300  # 5 minutes


async def get_quote(adapter: BrokerAdapter, symbol: str) -> Optional[Quote]:
    if adapter.broker_name == "angel_one":
        cache_key = f"shared:quote:{symbol}"
    else:
        cache_key = f"quote:{adapter.broker_name}:{symbol}"
    try:
        redis = get_redis()
    except RuntimeError:
        redis = None
    if redis:
        try:
            cached = await redis.get(cache_key)
            if cached:
                d = json.loads(cached)
                return Quote(**d)
        except Exception:
            redis = None

    quote = await adapter.get_quote(symbol)
    if quote and redis:
        try:
            await redis.setex(cache_key, _QUOTE_TTL, json.dumps(quote.__dict__))
        except Exception:
            pass
    return quote


async def get_quotes_batch(adapter: BrokerAdapter, symbols: List[str]) -> List[Quote]:
    try:
        redis = get_redis()
    except RuntimeError:
        redis = None

    # Shared cache for Angel One to reduce duplicate calls across users.
    if adapter.broker_name == "angel_one" and redis:
        out: List[Quote] = []
        missing: List[str] = []
        for symbol in symbols:
            key = f"shared:quote:{symbol}"
            cached = await redis.get(key)
            if cached:
                try:
                    out.append(Quote(**json.loads(cached)))
                    continue
                except Exception:
                    pass
            missing.append(symbol)

        if missing:
            fetched = await adapter.get_quotes_batch(missing)
            for quote in fetched:
                out.append(quote)
                try:
                    await redis.setex(f"shared:quote:{quote.symbol}", _QUOTE_TTL, json.dumps(quote.__dict__))
                except Exception:
                    pass
        return out

    quotes = await adapter.get_quotes_batch(symbols)
    if not quotes:
        logger.info("price_service.fallback_to_google_finance", count=len(symbols))
        quotes = await _gf_adapter.get_quotes_batch(symbols)
    return quotes


async def get_history(
    adapter: BrokerAdapter,
    symbol: str,
    period: str = "1y",
    interval: str = "1d",
) -> List[OHLCVBar]:
    cache_key = f"history:{adapter.broker_name}:{symbol}:{period}:{interval}"
    try:
        redis = get_redis()
    except RuntimeError:
        redis = None
    if redis:
        try:
            cached = await redis.get(cache_key)
            if cached:
                return [OHLCVBar(**b) for b in json.loads(cached)]
        except Exception:
            redis = None

    bars = await adapter.get_history(symbol, period, interval)
    if bars and redis:
        try:
            await redis.setex(cache_key, _HISTORY_TTL, json.dumps([b.__dict__ for b in bars]))
        except Exception:
            pass
    return bars


async def get_indices(adapter: BrokerAdapter) -> List[Quote]:
    cache_key = f"indices:{adapter.broker_name}"
    try:
        redis = get_redis()
    except RuntimeError:
        redis = None
    if redis:
        try:
            cached = await redis.get(cache_key)
            if cached:
                return [Quote(**q) for q in json.loads(cached)]
        except Exception:
            redis = None

    quotes = await adapter.get_indices()
    if not quotes:
        logger.info("price_service.indices_fallback_to_google_finance")
        quotes = await _gf_adapter.get_indices()
    if quotes and redis:
        try:
            await redis.setex(cache_key, _INDEX_TTL, json.dumps([q.__dict__ for q in quotes]))
        except Exception:
            pass
    return quotes
