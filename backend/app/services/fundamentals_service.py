"""Fundamentals service — Phase 3b.

Fetches fundamental financial data for NSE-listed equities using yfinance
and caches the result in Redis under ``fundamentals:<SYMBOL>`` (TTL 24h).

Data fetched per symbol
-----------------------
- pe_ratio          : Trailing P/E ratio
- pb_ratio          : Price-to-book ratio
- ev_to_ebitda      : Enterprise value / EBITDA
- debt_to_equity    : Total debt / total equity
- roe               : Return on equity (trailing twelve months)
- revenue_growth_yoy: Revenue growth year-over-year (%)
- earnings_growth   : EPS growth year-over-year (%)
- dividend_yield    : Annual dividend yield (%)
- market_cap_cr     : Market capitalisation in Indian crores (₹)

Quality gate: if fewer than 3 of the above fields are available (yfinance
returns None for many small-cap stocks), the function returns None and
nothing is cached — the signal generator skips the fundamentals phase for
that symbol rather than using partial/misleading data.

Redis cache key: ``fundamentals:<SYMBOL>``  (bare NSE ticker, no .NS suffix)
TTL: 86400 seconds (24h) — fundamentals change much more slowly than prices.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)

_FUNDAMENTALS_KEY_PREFIX = "fundamentals:"
_FUNDAMENTALS_TTL_SECS   = 86_400   # 24 hours
_MIN_VALID_FIELDS        = 3         # quality gate: skip symbols with fewer valid fields


def fetch_fundamentals(symbol: str) -> Optional[dict]:
    """Fetch fundamentals for a single NSE symbol from yfinance.

    Args:
        symbol: Bare NSE ticker, e.g. ``"RELIANCE"`` (without ``.NS`` suffix).

    Returns:
        Dict of fundamental features, or None if data is insufficient.
    """
    try:
        import yfinance as yf
    except ImportError:
        logger.error("fundamentals.yfinance_missing")
        return None

    ticker_code = symbol + ".NS"
    try:
        info = yf.Ticker(ticker_code).info
    except Exception as exc:
        logger.warning("fundamentals.yfinance_fetch_failed", symbol=symbol, err=str(exc))
        return None

    if not info:
        return None

    def _safe(key: str, scale: float = 1.0) -> Optional[float]:
        v = info.get(key)
        if v is None or not isinstance(v, (int, float)):
            return None
        try:
            result = float(v) * scale
            # Reject clearly invalid sentinel values yfinance occasionally returns
            if abs(result) > 1e12:
                return None
            return round(result, 4)
        except (ValueError, OverflowError):
            return None

    market_cap = info.get("marketCap")
    market_cap_cr: Optional[float] = None
    if market_cap and isinstance(market_cap, (int, float)) and market_cap > 0:
        market_cap_cr = round(float(market_cap) / 1e7, 2)   # ₹ → crores

    data: dict = {
        "pe_ratio":           _safe("trailingPE"),
        "pb_ratio":           _safe("priceToBook"),
        "ev_to_ebitda":       _safe("enterpriseToEbitda"),
        "debt_to_equity":     _safe("debtToEquity"),
        "roe":                _safe("returnOnEquity"),
        "revenue_growth_yoy": _safe("revenueGrowth"),
        "earnings_growth":    _safe("earningsGrowth"),
        "dividend_yield":     _safe("dividendYield"),
        "market_cap_cr":      market_cap_cr,
        "fetched_at":         datetime.now(tz=timezone.utc).isoformat(),
        "symbol":             symbol,
    }

    # Quality gate: require at least _MIN_VALID_FIELDS non-None values
    valid_count = sum(
        1 for k, v in data.items()
        if k not in ("fetched_at", "symbol") and v is not None
    )
    if valid_count < _MIN_VALID_FIELDS:
        logger.debug(
            "fundamentals.insufficient_data",
            symbol=symbol,
            valid_fields=valid_count,
            required=_MIN_VALID_FIELDS,
        )
        return None

    return data


def get_fundamentals_from_cache(symbol: str, redis_client=None) -> Optional[dict]:
    """Read fundamentals from Redis cache.

    Args:
        symbol: Bare NSE ticker.
        redis_client: optional pre-created sync Redis client.

    Returns:
        Fundamentals dict or None if cache miss / error.
    """
    try:
        if redis_client is None:
            import redis as _redis
            from app.core.config import settings
            redis_client = _redis.from_url(settings.redis_url, decode_responses=True)

        raw = redis_client.get(f"{_FUNDAMENTALS_KEY_PREFIX}{symbol}")
        if raw is None:
            return None
        return json.loads(raw)
    except Exception as exc:
        logger.warning("fundamentals.cache_read_failed", symbol=symbol, err=str(exc))
        return None


def cache_fundamentals(symbol: str, data: dict, redis_client=None) -> bool:
    """Write fundamentals dict to Redis with TTL.

    Returns True on success, False on error.
    """
    try:
        if redis_client is None:
            import redis as _redis
            from app.core.config import settings
            redis_client = _redis.from_url(settings.redis_url, decode_responses=True)

        redis_client.setex(
            f"{_FUNDAMENTALS_KEY_PREFIX}{symbol}",
            _FUNDAMENTALS_TTL_SECS,
            json.dumps(data),
        )
        return True
    except Exception as exc:
        logger.warning("fundamentals.cache_write_failed", symbol=symbol, err=str(exc))
        return False


def fetch_and_cache(symbol: str, redis_client=None) -> Optional[dict]:
    """Convenience wrapper: fetch from yfinance and immediately cache.

    Returns the fundamentals dict, or None if fetch failed the quality gate.
    """
    data = fetch_fundamentals(symbol)
    if data is not None:
        cache_fundamentals(symbol, data, redis_client=redis_client)
    return data


def score_fundamentals(data: dict) -> float:
    """Convert a fundamentals dict into a single blended score in [−1, +1].

    Higher score = more attractive fundamental picture.

    Scoring logic
    -------------
    P/E:          < 15 → +0.2, 15–30 → 0, > 30 → −0.1
    P/B:          < 1  → +0.2, 1–3   → 0, > 5  → −0.15
    ROE:          > 20% → +0.2, 10–20% → +0.1, < 0 → −0.2
    Debt/Equity:  < 0.5 → +0.15, 0.5–1.5 → 0, > 2 → −0.15
    Revenue growth: > 15% → +0.15, 0–15% → 0, < 0 → −0.1
    Dividend yield: > 2% → +0.1 (quality/stability bonus)

    The raw sum is clamped to [−1, +1].
    """
    score = 0.0

    pe = data.get("pe_ratio")
    if pe is not None:
        if pe < 15:
            score += 0.20
        elif pe > 30:
            score -= 0.10

    pb = data.get("pb_ratio")
    if pb is not None:
        if pb < 1.0:
            score += 0.20
        elif pb > 5.0:
            score -= 0.15

    roe = data.get("roe")
    if roe is not None:
        if roe > 0.20:
            score += 0.20
        elif roe > 0.10:
            score += 0.10
        elif roe < 0:
            score -= 0.20

    de = data.get("debt_to_equity")
    if de is not None:
        if de < 50:           # yfinance returns as %, e.g. 45 means 0.45
            score += 0.15
        elif de > 200:
            score -= 0.15

    rev_growth = data.get("revenue_growth_yoy")
    if rev_growth is not None:
        if rev_growth > 0.15:
            score += 0.15
        elif rev_growth < 0:
            score -= 0.10

    div_yield = data.get("dividend_yield")
    if div_yield is not None and div_yield > 0.02:
        score += 0.10

    return round(max(-1.0, min(1.0, score)), 4)
