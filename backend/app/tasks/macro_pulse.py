"""Macro pulse pipeline task — produces market regime keys in Redis.

Runs on Celery beat schedule. Fetches macro features, detects regime,
and publishes to Redis keys consumed by the signal generator and explainer.

Redis keys written:
  - ``macro:sentiment:regime``  — "risk_on" | "risk_off" | "neutral"
  - ``macro:top_events``        — JSON list of recent macro observations
"""
from __future__ import annotations

import json
import asyncio
import logging

from app.tasks.celery_app import celery_app

logger = logging.getLogger(__name__)

_REGIME_KEY = "macro:sentiment:regime"
_EVENTS_KEY = "macro:top_events"
_TTL = 7200  # 2 hours


@celery_app.task(name="app.tasks.macro_pulse.update_macro_regime", bind=True)
def update_macro_regime(self) -> dict:
    """Fetch macro features, detect regime, cache in Redis."""
    try:
        import redis as _redis
        from app.core.config import settings

        r = _redis.from_url(settings.redis_url, decode_responses=True)

        # Fetch macro features (async → sync bridge)
        from app.services.macro_features import fetch_macro_features
        features = asyncio.run(fetch_macro_features())

        # Detect regime
        from app.services.regime_detector import detect_regime
        regime = detect_regime(features)

        # Write to Redis
        r.setex(_REGIME_KEY, _TTL, regime)

        events = []
        if features.get("vix", 18) > 22:
            events.append(f"India VIX elevated at {features['vix']}")
        if features.get("nifty_20d_return", 0) < -0.03:
            events.append(f"Nifty 50 down {features['nifty_20d_return']*100:.1f}% over 20 days")
        if features.get("crude_price", 80) > 90:
            events.append(f"Crude oil at ${features['crude_price']}")
        if features.get("us10y_yield", 4.3) > 4.8:
            events.append(f"US 10Y yield at {features['us10y_yield']}%")

        r.setex(_EVENTS_KEY, _TTL, json.dumps(events))

        logger.info("macro_pulse.updated", regime=regime, events=len(events))
        return {"regime": regime, "events": events, "features": features}

    except Exception as exc:
        logger.error("macro_pulse.failed", err=str(exc))
        return {"error": str(exc)}
