"""Feature engineering service — Phase 3.

Builds a flat feature vector per symbol from:
  - OHLCV technical indicators  (RSI, MACD, Bollinger, ATR, OBV, ADX)
  - Phase 4 sentiment score     (from Redis ``sentiment:<SYM>`` cache)

The output is a dict[str, float] ready for LightGBM / sklearn pipelines.
All maths is pure-Python + numpy (already present for yfinance/pandas);
no additional ML library is needed in this file.
"""
from __future__ import annotations

import json
import math
from typing import Optional

import numpy as np
import structlog

logger = structlog.get_logger(__name__)

# ── Feature name constants (order matters for model compatibility) ─────────────
FEATURE_NAMES: list[str] = [
    "rsi_14",
    "macd_hist",         # MACD histogram (MACD line - signal line)
    "bb_pct_b",          # Bollinger %B — where close sits in band [0,1]
    "atr_pct",           # ATR(14) as % of close (normalised volatility)
    "obv_trend",         # OBV 5-bar momentum (OBV[-1] - OBV[-5]) / |OBV[-5]|
    "adx_14",            # Average Directional Index — trend strength
    "volume_ratio",      # today's volume / 20-day avg volume
    "close_vs_sma20",    # (close - SMA20) / SMA20
    "close_vs_sma50",    # (close - SMA50) / SMA50
    "sentiment_score",   # Phase 4 rolling 24-h weighted sentiment [-1, 1]
    # Phase 3b additions — added without breaking old models (appended at end)
    "momentum_1m",       # 21-bar price return: (close[-1] / close[-22]) - 1
    "momentum_3m",       # 63-bar price return: (close[-1] / close[-64]) - 1
    "hist_vol_20d",      # 20-day realised volatility (annualised std of log returns)
    "week52_proximity",  # (close - 52w_low) / (52w_high - 52w_low); 0=at low, 1=at high
]


# ══════════════════════════════════════════════════════════════════════════════
#  Pure maths helpers (no external dependencies beyond numpy)
# ══════════════════════════════════════════════════════════════════════════════

def _ema(arr: np.ndarray, period: int) -> np.ndarray:
    k = 2.0 / (period + 1)
    out = np.empty_like(arr, dtype=float)
    out[:period - 1] = np.nan
    out[period - 1] = arr[:period].mean()
    for i in range(period, len(arr)):
        out[i] = arr[i] * k + out[i - 1] * (1 - k)
    return out


def _rsi(closes: np.ndarray, period: int = 14) -> float:
    if len(closes) < period + 1:
        return float("nan")
    deltas = np.diff(closes)
    gains  = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)
    avg_g = gains[:period].mean()
    avg_l = losses[:period].mean()
    for g, l in zip(gains[period:], losses[period:]):
        avg_g = (avg_g * (period - 1) + g) / period
        avg_l = (avg_l * (period - 1) + l) / period
    if avg_l == 0:
        return 100.0
    return 100.0 - 100.0 / (1 + avg_g / avg_l)


def _macd_hist(closes: np.ndarray) -> float:
    """MACD histogram = (EMA12 - EMA26) - EMA9(EMA12 - EMA26)."""
    if len(closes) < 35:
        return float("nan")
    e12  = _ema(closes, 12)
    e26  = _ema(closes, 26)
    macd = e12 - e26
    # Only compute signal on the non-nan portion
    valid_start = 25  # first valid MACD index
    signal = _ema(macd[valid_start:], 9)
    if len(signal) == 0 or np.isnan(signal[-1]):
        return float("nan")
    return float(macd[-1] - signal[-1])


def _bollinger_pct_b(closes: np.ndarray, period: int = 20) -> float:
    if len(closes) < period:
        return float("nan")
    window = closes[-period:]
    mid = window.mean()
    std = window.std()
    if std == 0:
        return 0.5
    upper = mid + 2 * std
    lower = mid - 2 * std
    return float((closes[-1] - lower) / (upper - lower))


def _atr_pct(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> float:
    if len(closes) < period + 1:
        return float("nan")
    tr = np.maximum(
        highs[1:] - lows[1:],
        np.maximum(
            np.abs(highs[1:] - closes[:-1]),
            np.abs(lows[1:]  - closes[:-1]),
        ),
    )
    atr = tr[-period:].mean()
    return float(atr / closes[-1]) if closes[-1] > 0 else float("nan")


def _obv_trend(closes: np.ndarray, volumes: np.ndarray) -> float:
    if len(closes) < 6:
        return float("nan")
    signs = np.sign(np.diff(closes))
    obv   = np.concatenate([[0.0], np.cumsum(signs * volumes[1:])])
    base  = obv[-5]
    if base == 0:
        return 0.0
    return float((obv[-1] - base) / abs(base))


def _adx(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> float:
    if len(closes) < (period * 2 + 1):
        return float("nan")
    h, l, c = highs, lows, closes
    tr  = np.maximum(h[1:] - l[1:], np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])))
    pdm = np.where((h[1:] - h[:-1]) > (l[:-1] - l[1:]), np.maximum(h[1:] - h[:-1], 0), 0.0)
    ndm = np.where((l[:-1] - l[1:]) > (h[1:] - h[:-1]), np.maximum(l[:-1] - l[1:], 0), 0.0)

    def _wma(arr):
        out = np.empty(len(arr) - period + 1)
        out[0] = arr[:period].sum()
        for i in range(1, len(out)):
            out[i] = out[i - 1] - out[i - 1] / period + arr[period + i - 1]
        return out

    atr14 = _wma(tr);  pdm14 = _wma(pdm);  ndm14 = _wma(ndm)
    with np.errstate(invalid="ignore", divide="ignore"):
        pdi = 100 * pdm14 / atr14
        ndi = 100 * ndm14 / atr14
        dx  = 100 * np.abs(pdi - ndi) / (pdi + ndi)
    dx = np.nan_to_num(dx)
    if len(dx) < period:
        return float("nan")
    adx_val = dx[-period:].mean()
    return float(adx_val)


def _sma_ratio(closes: np.ndarray, period: int) -> float:
    if len(closes) < period:
        return float("nan")
    sma = closes[-period:].mean()
    if sma == 0:
        return float("nan")
    return float((closes[-1] - sma) / sma)


def _momentum(closes: np.ndarray, lookback: int) -> float:
    """Simple price momentum: (close[-1] / close[-lookback-1]) - 1."""
    if len(closes) < lookback + 1:
        return float("nan")
    base = closes[-(lookback + 1)]
    if base <= 0:
        return float("nan")
    return float(closes[-1] / base - 1)


def _hist_vol_20d(closes: np.ndarray) -> float:
    """20-day realised volatility (annualised std of daily log returns)."""
    if len(closes) < 21:
        return float("nan")
    log_rets = np.diff(np.log(closes[-21:]))
    std_daily = float(np.std(log_rets, ddof=1))
    return std_daily * np.sqrt(252)   # annualise


def _week52_proximity(closes: np.ndarray) -> float:
    """Position of current close within the 52-week high/low range [0, 1]."""
    if len(closes) < 252:
        window = closes          # use all available if < 1 year
    else:
        window = closes[-252:]
    lo  = float(window.min())
    hi  = float(window.max())
    if hi == lo:
        return 0.5
    return float((closes[-1] - lo) / (hi - lo))


def _volume_ratio(volumes: np.ndarray, period: int = 20) -> float:
    if len(volumes) < period + 1:
        return float("nan")
    avg = volumes[-period - 1:-1].mean()
    if avg == 0:
        return float("nan")
    return float(volumes[-1] / avg)


# ══════════════════════════════════════════════════════════════════════════════
#  Public interface
# ══════════════════════════════════════════════════════════════════════════════

def build_features(
    symbol: str,
    closes:  list[float],
    highs:   list[float],
    lows:    list[float],
    volumes: list[float],
    sentiment_score: Optional[float] = None,
) -> dict[str, float]:
    """Return a feature dict for ``symbol`` using provided OHLCV series.

    The returned dict has exactly the keys in ``FEATURE_NAMES``.
    Missing / uncomputable features are set to ``nan`` so the caller
    can decide whether to skip or impute.

    Parameters
    ----------
    closes, highs, lows, volumes:
        Time-ordered arrays, oldest first, most-recent last.
        At least 60 bars recommended for all features to be valid.
    sentiment_score:
        Pre-fetched Phase 4 rolling sentiment for this symbol [-1, 1].
        ``None`` → 0.0 (neutral default).
    """
    c = np.array(closes,  dtype=float)
    h = np.array(highs,   dtype=float)
    l = np.array(lows,    dtype=float)
    v = np.array(volumes,  dtype=float)

    feats: dict[str, float] = {
        "rsi_14":        _rsi(c),
        "macd_hist":     _macd_hist(c),
        "bb_pct_b":      _bollinger_pct_b(c),
        "atr_pct":       _atr_pct(h, l, c),
        "obv_trend":     _obv_trend(c, v),
        "adx_14":        _adx(h, l, c),
        "volume_ratio":  _volume_ratio(v),
        "close_vs_sma20": _sma_ratio(c, 20),
        "close_vs_sma50": _sma_ratio(c, 50),
        "sentiment_score": float(sentiment_score) if sentiment_score is not None else 0.0,
        # Phase 3b — momentum / volatility / 52-week range
        "momentum_1m":       _momentum(c, 21),
        "momentum_3m":       _momentum(c, 63),
        "hist_vol_20d":      _hist_vol_20d(c),
        "week52_proximity":  _week52_proximity(c),
    }

    valid = sum(1 for v in feats.values() if not math.isnan(v))
    logger.debug("feature_engineer.built", symbol=symbol, valid_features=valid, total=len(feats))
    return feats


async def build_features_for_symbol(
    symbol: str,
    session,          # AsyncSession
    redis_client,     # aioredis client
    lookback_days: int = 90,
) -> Optional[dict[str, float]]:
    """Convenience wrapper: fetch OHLCV from DB and sentiment from Redis,
    then call ``build_features``.

    Returns ``None`` if insufficient OHLCV history.
    """
    from sqlalchemy import text as _text

    # ── 1. Fetch OHLCV ────────────────────────────────────────────────────────
    rows = (await session.execute(
        _text("""
            SELECT ts, open, high, low, close, volume
            FROM   ohlcv_daily
            WHERE  symbol = :sym
            ORDER  BY ts DESC
            LIMIT  :lim
        """),
        {"sym": symbol, "lim": lookback_days},
    )).fetchall()

    if len(rows) < 30:
        logger.debug("feature_engineer.insufficient_history", symbol=symbol, bars=len(rows))
        return None

    # Rows are newest-first from DB; reverse to oldest-first for maths
    rows = list(reversed(rows))
    closes  = [float(r[4]) for r in rows]
    highs   = [float(r[2]) for r in rows]
    lows    = [float(r[3]) for r in rows]
    volumes = [float(r[5]) for r in rows]

    # ── 2. Fetch sentiment from Redis (Phase 4 cache) ─────────────────────────
    sentiment: Optional[float] = None
    try:
        raw = await redis_client.get(f"sentiment:{symbol}")
        if raw:
            data = json.loads(raw)
            sentiment = float(data.get("score", 0.0))
    except Exception as exc:
        logger.warning("feature_engineer.sentiment_fetch_failed", symbol=symbol, err=str(exc))

    return build_features(symbol, closes, highs, lows, volumes, sentiment)
