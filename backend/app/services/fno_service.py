"""F&O (Futures & Options) service — Phase 10 Institutional Upgrade.

Fetches Put-Call Ratio (PCR) and Open Interest (OI) momentum for a symbol
from the NSE option chain endpoint and returns a signal score in [-1, 1].

Redis cache key: ``fno:pcr:{SYMBOL}``  TTL: 6 hours
(refreshed nightly by fno_ingest task; stale data returns cached value)

Score interpretation:
  +1.0  — high PCR (> 1.5) → market has heavy put protection → bullish contrarian
  -1.0  — low PCR  (< 0.5) → excessive call buying → bearish contrarian
   0.0  — neutral PCR (~1.0) or no F&O data available
"""
from __future__ import annotations

import json
import asyncio
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)

# Redis TTL — 6 hours (F&O data is daily; this prevents stale data overnight)
_CACHE_TTL = 6 * 3600

# PCR thresholds for scoring
_PCR_BULL_THRESHOLD = 1.3   # above this → bullish (heavy put buying = fear = contrarian BUY)
_PCR_BEAR_THRESHOLD = 0.7   # below this → bearish (call speculation = greed = contrarian SELL)


# ══════════════════════════════════════════════════════════════════════════════
#  NSE Option Chain Fetcher
# ══════════════════════════════════════════════════════════════════════════════

async def _fetch_option_chain(symbol: str) -> Optional[dict]:
    """Fetch NSE option chain data for a symbol.

    Uses curl_cffi for TLS fingerprint bypass (same pattern as NSEIndiaAdapter).
    Returns the raw JSON or None on failure.
    """
    try:
        from curl_cffi.requests import AsyncSession
        url = f"https://www.nseindia.com/api/option-chain-equities?symbol={symbol}"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "en-US,en;q=0.9",
            "Referer": "https://www.nseindia.com/",
        }
        async with AsyncSession(impersonate="chrome124", timeout=15) as session:
            # Warm up the session cookie
            await session.get("https://www.nseindia.com/", headers=headers)
            resp = await session.get(url, headers=headers)
            if resp.status_code == 200:
                return resp.json()
            logger.debug("fno_service.http_error", symbol=symbol, status=resp.status_code)
            return None
    except Exception as exc:
        logger.debug("fno_service.fetch_failed", symbol=symbol, err=str(exc))
        return None


def _compute_pcr(option_chain: dict) -> Optional[dict]:
    """Extract PCR and total OI from NSE option chain response.

    Returns:
        dict with keys: pcr_ratio, total_call_oi, total_put_oi, total_oi
        or None if the data is malformed.
    """
    try:
        records = option_chain.get("records", {}).get("data", [])
        if not records:
            return None

        total_call_oi = 0
        total_put_oi = 0

        for record in records:
            ce = record.get("CE", {})
            pe = record.get("PE", {})
            total_call_oi += ce.get("openInterest", 0)
            total_put_oi  += pe.get("openInterest", 0)

        if total_call_oi == 0:
            return None

        pcr = round(total_put_oi / total_call_oi, 4)
        return {
            "pcr_ratio":     pcr,
            "total_call_oi": total_call_oi,
            "total_put_oi":  total_put_oi,
            "total_oi":      total_call_oi + total_put_oi,
        }
    except Exception as exc:
        logger.debug("fno_service.pcr_parse_failed", err=str(exc))
        return None


# ══════════════════════════════════════════════════════════════════════════════
#  Public API
# ══════════════════════════════════════════════════════════════════════════════

async def get_fno_metrics_cached(
    symbol: str,
    force_refresh: bool = False,
) -> Optional[dict]:
    """Return F&O metrics for symbol, using Redis cache.

    Parameters
    ----------
    symbol:
        NSE equity symbol (no .NS suffix).
    force_refresh:
        Bypass cache and re-fetch from NSE (used by fno_ingest task).

    Returns
    -------
    dict with ``pcr_ratio``, ``total_call_oi``, ``total_put_oi``, ``total_oi``
    or None if the symbol has no F&O data or fetch failed.
    """
    import redis.asyncio as aioredis
    from app.core.config import settings

    cache_key = f"fno:pcr:{symbol}"
    r = aioredis.from_url(settings.redis_url, decode_responses=True)

    try:
        if not force_refresh:
            cached = await r.get(cache_key)
            if cached:
                return json.loads(cached)

        # Fetch from NSE
        raw = await _fetch_option_chain(symbol)
        if raw is None:
            return None

        metrics = _compute_pcr(raw)
        if metrics is None:
            return None

        # Cache for 6 hours
        await r.setex(cache_key, _CACHE_TTL, json.dumps(metrics))
        logger.debug("fno_service.cached", symbol=symbol, pcr=metrics["pcr_ratio"])
        return metrics

    except Exception as exc:
        logger.warning("fno_service.error", symbol=symbol, err=str(exc))
        return None
    finally:
        await r.aclose()


def score_fno(metrics: Optional[dict]) -> float:
    """Convert F&O metrics into a signal score in [-1.0, 1.0].

    Uses contrarian PCR interpretation:
    - High PCR (> 1.3): heavy put buying = fear = contrarian BUY → positive score
    - Low  PCR (< 0.7): heavy call buying = greed = contrarian SELL → negative score
    - Mid  PCR: neutral

    Returns 0.0 if metrics is None (no F&O data for this symbol).
    """
    if not metrics:
        return 0.0

    pcr = metrics.get("pcr_ratio", 1.0)

    if pcr >= _PCR_BULL_THRESHOLD:
        # Scale: PCR=1.3 → 0.0, PCR=2.0 → 1.0 (capped)
        score = min((pcr - _PCR_BULL_THRESHOLD) / (2.0 - _PCR_BULL_THRESHOLD), 1.0)
    elif pcr <= _PCR_BEAR_THRESHOLD:
        # Scale: PCR=0.7 → 0.0, PCR=0.0 → -1.0 (capped)
        score = -min((_PCR_BEAR_THRESHOLD - pcr) / _PCR_BEAR_THRESHOLD, 1.0)
    else:
        # Linear interpolation in the neutral band [0.7, 1.3]
        mid = (_PCR_BULL_THRESHOLD + _PCR_BEAR_THRESHOLD) / 2   # 1.0
        score = (pcr - mid) / (mid - _PCR_BEAR_THRESHOLD)       # small ± value

    return round(float(score), 4)
