"""Macro-economic feature service for signal enrichment.

Fetches and caches macro indicators used by the regime detector and
feature engineering pipeline:
  - India VIX
  - Nifty 50 20-day return
  - USD/INR exchange rate
  - Crude oil (Brent)
  - US 10Y Treasury yield

Data source: yfinance (free, no API key).
Cached in Redis for 1 hour to avoid repeated API calls.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Dict, Optional

import structlog

logger = structlog.get_logger(__name__)

_CACHE_KEY = "macro:features"
_CACHE_TTL = 3600  # 1 hour

_SYMBOLS = {
    "vix": "^INDIAVIX",
    "nifty50": "^NSEI",
    "usdinr": "USDINR=X",
    "crude": "BZ=F",        # Brent crude futures
    "us10y": "^TNX",         # US 10Y yield
}


async def fetch_macro_features(
    redis=None,
) -> Dict[str, float]:
    """Fetch current macro features. Returns cached if available.

    Returns dict with keys:
        vix, nifty_20d_return, usdinr, crude_price, us10y_yield
    """
    # Try Redis cache first
    if redis:
        try:
            cached = await redis.get(_CACHE_KEY)
            if cached:
                return json.loads(cached)
        except Exception:
            pass

    features = await _fetch_from_yfinance()

    # Cache in Redis
    if redis and features:
        try:
            await redis.setex(_CACHE_KEY, _CACHE_TTL, json.dumps(features))
        except Exception:
            pass

    return features


async def _fetch_from_yfinance() -> Dict[str, float]:
    """Fetch macro data from yfinance."""
    import asyncio

    features: Dict[str, float] = {
        "vix": 18.0,
        "nifty_20d_return": 0.0,
        "usdinr": 83.5,
        "crude_price": 80.0,
        "us10y_yield": 4.3,
    }

    try:
        import yfinance as yf

        loop = asyncio.get_running_loop()

        def _download():
            result = {}
            end = datetime.now()
            start = end - timedelta(days=30)

            try:
                vix = yf.download("^INDIAVIX", start=start, end=end, progress=False)
                if len(vix) > 0:
                    result["vix"] = round(float(vix["Close"].iloc[-1]), 2)
            except Exception:
                pass

            try:
                nifty = yf.download("^NSEI", start=start, end=end, progress=False)
                if len(nifty) >= 20:
                    closes = nifty["Close"]
                    ret_20d = (closes.iloc[-1] - closes.iloc[-20]) / closes.iloc[-20]
                    result["nifty_20d_return"] = round(float(ret_20d), 4)
            except Exception:
                pass

            try:
                usd = yf.download("USDINR=X", start=start, end=end, progress=False)
                if len(usd) > 0:
                    result["usdinr"] = round(float(usd["Close"].iloc[-1]), 2)
            except Exception:
                pass

            try:
                crude = yf.download("BZ=F", start=start, end=end, progress=False)
                if len(crude) > 0:
                    result["crude_price"] = round(float(crude["Close"].iloc[-1]), 2)
            except Exception:
                pass

            try:
                tnx = yf.download("^TNX", start=start, end=end, progress=False)
                if len(tnx) > 0:
                    result["us10y_yield"] = round(float(tnx["Close"].iloc[-1]), 2)
            except Exception:
                pass

            return result

        fetched = await loop.run_in_executor(None, _download)
        features.update(fetched)

    except ImportError:
        logger.warning("macro_features.yfinance_not_installed")
    except Exception as exc:
        logger.warning("macro_features.fetch_failed", err=str(exc))

    return features
