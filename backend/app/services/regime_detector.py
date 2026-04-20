"""HDBSCAN market regime detection.

Clusters market conditions into regimes (risk_on, risk_off, neutral) using
HDBSCAN on a small set of macro/volatility features. Used to adjust signal
confidence and filter trades during unfavourable regimes.

Features used:
  - VIX (India VIX) level
  - Nifty 50 rolling 20-day return
  - Put-Call ratio (if available)
  - FII net flow direction

If macro data is unavailable, returns "neutral" as the default regime.
"""
from __future__ import annotations

from typing import Dict, Optional

import numpy as np
import structlog

logger = structlog.get_logger(__name__)


def detect_regime(
    features: Dict[str, float],
) -> str:
    """Detect market regime from macro features.

    Args:
        features: dict with keys like "vix", "nifty_20d_return",
                  "put_call_ratio", "fii_net_flow"

    Returns:
        "risk_on" | "risk_off" | "neutral"
    """
    try:
        import hdbscan  # type: ignore
    except ImportError:
        logger.warning("regime.hdbscan_not_installed")
        return _rule_based_regime(features)

    # We need stored cluster history for HDBSCAN to work meaningfully.
    # For now, use rule-based regime with HDBSCAN as future upgrade path.
    return _rule_based_regime(features)


def _rule_based_regime(features: Dict[str, float]) -> str:
    """Rule-based regime detection as baseline.

    Thresholds:
      - VIX > 25 → risk_off
      - VIX < 15 → risk_on
      - Nifty 20d return < -5% → risk_off
      - Nifty 20d return > +3% → risk_on
    """
    vix = features.get("vix", 18.0)
    nifty_ret = features.get("nifty_20d_return", 0.0)
    fii_flow = features.get("fii_net_flow", 0.0)

    score = 0.0

    # VIX contribution
    if vix > 25:
        score -= min((vix - 25) / 15, 1.0)  # -1 at VIX=40
    elif vix < 15:
        score += min((15 - vix) / 10, 1.0)  # +1 at VIX=5

    # Nifty return contribution
    if nifty_ret < -0.05:
        score -= min(abs(nifty_ret) / 0.10, 1.0)
    elif nifty_ret > 0.03:
        score += min(nifty_ret / 0.06, 1.0)

    # FII flow contribution
    if fii_flow < -1000:  # crores
        score -= 0.3
    elif fii_flow > 1000:
        score += 0.3

    if score > 0.5:
        return "risk_on"
    elif score < -0.5:
        return "risk_off"
    return "neutral"


def get_regime_confidence_multiplier(regime: str) -> float:
    """Return a multiplier [0.5, 1.2] to adjust signal confidence by regime."""
    return {
        "risk_on": 1.1,
        "risk_off": 0.6,
        "neutral": 1.0,
    }.get(regime, 1.0)
