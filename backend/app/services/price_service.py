"""Price service — orchestration layer between API endpoints and data adapters.

Live price chain:    NSE India API (primary) → Broker API (fallback)
Historical chain:    YFinance only (free, years of NSE data, delay irrelevant)

All live quote caches are keyed by symbol only (not per-broker), so a single
NSE fetch warms the cache for all concurrent users simultaneously.
"""
from __future__ import annotations

import json
from typing import List, Optional

import structlog

from app.brokers.base import BrokerAdapter, OHLCVBar, Quote
from app.brokers.nse_india_adapter import NSEIndiaAdapter
from app.core.redis_client import get_redis

# Module-level NSE singleton — one session shared across all requests
_nse_adapter = NSEIndiaAdapter()

logger = structlog.get_logger(__name__)

_QUOTE_TTL   = 15   # seconds — matches WebSocket push interval so every tick is fresh
_INDEX_TTL   = 15   # seconds — same reasoning
_HISTORY_TTL = 300  # 5 minutes — historical data changes slowly


def _get_redis_safe():
    try:
        return get_redis()
    except RuntimeError:
        return None


async def _redis_get(redis, key: str):
    try:
        return await redis.get(key)
    except Exception:
        return None


async def _redis_setex(redis, key: str, ttl: int, value: str) -> None:
    try:
        await redis.setex(key, ttl, value)
    except Exception:
        pass


async def get_quote(adapter: BrokerAdapter, symbol: str) -> Optional[Quote]:
    """Fetch a single live quote: NSE primary → broker fallback."""
    clean = symbol.upper().replace(".NS", "").replace(".BO", "")
    cache_key = f"nse:quote:{clean}"
    redis = _get_redis_safe()

    if redis:
        cached = await _redis_get(redis, cache_key)
        if cached:
            return Quote(**json.loads(cached))

    # 1. NSE India primary
    quote = await _nse_adapter.get_quote(symbol)

    # 2. Broker fallback
    if not quote:
        logger.info("price_service.nse_quote_miss_broker_fallback", symbol=symbol)
        quote = await adapter.get_quote(symbol)

    if quote and redis:
        await _redis_setex(redis, cache_key, _QUOTE_TTL, json.dumps(quote.__dict__))
    return quote


async def get_quotes_batch(adapter: BrokerAdapter, symbols: List[str]) -> List[Quote]:
    """Fetch live quotes for multiple symbols: NSE primary → broker fallback.

    Cache is shared across all users — one NSE call serves everyone within the TTL.
    """
    cache_key = f"nse:quotes:{','.join(sorted(s.upper().replace('.NS','').replace('.BO','') for s in symbols))}"
    redis = _get_redis_safe()

    if redis:
        cached = await _redis_get(redis, cache_key)
        if cached:
            return [Quote(**q) for q in json.loads(cached)]

    # 1. NSE India primary — one bulk call for all Nifty 50 symbols
    quotes = await _nse_adapter.get_quotes_batch(symbols)

    # 2. Broker fallback — only for symbols NSE missed
    fetched_syms = {q.symbol for q in quotes}
    missing = [
        s for s in symbols
        if s.upper().replace(".NS", "").replace(".BO", "") not in fetched_syms
    ]
    if missing:
        try:
            broker_quotes = await adapter.get_quotes_batch(missing)
            if broker_quotes:
                quotes.extend(broker_quotes)
                logger.info(
                    "price_service.broker_filled_missing",
                    count=len(broker_quotes),
                    symbols=[q.symbol for q in broker_quotes],
                )
        except Exception as exc:
            logger.warning("price_service.broker_fallback_failed", err=str(exc))

    if quotes and redis:
        await _redis_setex(redis, cache_key, _QUOTE_TTL, json.dumps([q.__dict__ for q in quotes]))
    return quotes


async def get_indices(adapter: BrokerAdapter) -> List[Quote]:
    """Fetch live index quotes: NSE primary → broker fallback."""
    cache_key = "nse:indices"
    redis = _get_redis_safe()

    if redis:
        cached = await _redis_get(redis, cache_key)
        if cached:
            return [Quote(**q) for q in json.loads(cached)]

    # 1. NSE India primary
    quotes = await _nse_adapter.get_indices()

    # 2. Broker fallback
    if not quotes:
        logger.info("price_service.nse_indices_miss_broker_fallback")
        quotes = await adapter.get_indices()

    if quotes and redis:
        await _redis_setex(redis, cache_key, _INDEX_TTL, json.dumps([q.__dict__ for q in quotes]))
    return quotes


async def get_history(
    adapter: BrokerAdapter,
    symbol: str,
    period: str = "1y",
    interval: str = "1d",
) -> List[OHLCVBar]:
    """Fetch historical OHLCV bars — YFinance is the sole source.

    YFinance provides years of free NSE historical data. The 15-minute
    delay is irrelevant for historical backfill, ML training, and charting.
    Broker APIs and NSE India are not used for history.
    """
    cache_key = f"history:{symbol.upper()}:{period}:{interval}"
    redis = _get_redis_safe()

    if redis:
        cached = await _redis_get(redis, cache_key)
        if cached:
            return [OHLCVBar(**b) for b in json.loads(cached)]

    # YFinance — sole source for historical data
    from app.brokers.yfinance_adapter import YFinanceAdapter  # noqa: PLC0415
    bars = await YFinanceAdapter().get_history(symbol, period, interval)
    if bars:
        logger.info("price_service.yfinance_history_fetched", symbol=symbol, bars=len(bars))
    else:
        logger.warning("price_service.yfinance_history_empty", symbol=symbol, period=period)

    if bars and redis:
        await _redis_setex(redis, cache_key, _HISTORY_TTL, json.dumps([b.__dict__ for b in bars]))
    return bars
