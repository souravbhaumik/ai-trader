"""Technical-indicator signal generation task — Phase 2.

Runs after EOD data ingestion (4:45 PM IST Mon–Fri).  Reads the last
N days of daily OHLCV from ``ohlcv_daily``, computes three indicators,
and upserts confirmed signals into the ``signals`` table.

Indicators
----------
* RSI(14)       — oversold < 30 → BUY ; overbought > 70 → SELL
* MACD(12,26,9) — signal-line crossover  (bullish → BUY ; bearish → SELL)
* Bollinger(20,2) — close outside band (below lower → BUY ; above upper → SELL)

A symbol produces at most **one active signal per run**.  When two or
more indicators agree the confidence is boosted.  If indicators
conflict the signal is dropped (net confidence < threshold).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import structlog

from app.tasks.celery_app import celery_app

logger = structlog.get_logger(__name__)

# ── Tunables ──────────────────────────────────────────────────────────────────
_LOOKBACK       = 60    # calendar days fetched per symbol (enough for MACD-26)
_MIN_BARS       = 28    # minimum valid rows needed (skip thin history)
_CONFIDENCE_MIN = 0.25  # signals below this threshold are discarded
_BATCH_SIZE     = 20    # symbols fetched from DB per loop iteration
_BUY_TARGET_PCT = 0.05  # expected upside for BUY  (5 %)
_BUY_SL_PCT     = 0.03  # stop-loss for BUY         (3 %)
_SELL_TARGET_PCT = 0.05 # expected downside for SELL (5 %)
_SELL_SL_PCT     = 0.03 # stop-loss for SELL         (3 %)
_MODEL_VERSION  = "technical-v1"


# ══════════════════════════════════════════════════════════════════════════════
#  Pure-Python indicator maths (no pandas / numpy dependency in worker)
# ══════════════════════════════════════════════════════════════════════════════

def _ema(values: List[float], period: int) -> List[float]:
    """Exponential moving average — returns list same length as ``values``."""
    if len(values) < period:
        return [float("nan")] * len(values)
    k = 2.0 / (period + 1)
    result: List[float] = [float("nan")] * (period - 1)
    # seed with simple average of first `period` elements
    seed = sum(values[:period]) / period
    result.append(seed)
    for v in values[period:]:
        result.append(v * k + result[-1] * (1 - k))
    return result


def _rsi(closes: List[float], period: int = 14) -> Optional[float]:
    """RSI of the most-recent bar.  Returns None when history is too short."""
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        delta = closes[i] - closes[i - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))
    # Wilder smoothed average (simple seed then exponential)
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for g, l in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + g) / period
        avg_loss = (avg_loss * (period - 1) + l) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1 + rs))


def _macd(closes: List[float]) -> Tuple[Optional[float], Optional[float]]:
    """Return (macd_line, signal_line) for the most-recent bar.

    Uses standard 12/26/9 parameters.
    Returns (None, None) when history is too short.
    """
    if len(closes) < 35:   # 26 EMA + 9 signal EMA minimum
        return None, None
    ema12 = _ema(closes, 12)
    ema26 = _ema(closes, 26)
    macd_line = [
        (m - e) if (m == m and e == e) else float("nan")
        for m, e in zip(ema12, ema26)
    ]
    valid = [v for v in macd_line if v == v]  # drop NaN
    if len(valid) < 9:
        return None, None
    signal_ema = _ema(valid, 9)
    return valid[-1], signal_ema[-1]


def _bollinger(closes: List[float], period: int = 20) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """Return (upper, middle, lower) Bollinger bands for the most-recent bar."""
    if len(closes) < period:
        return None, None, None
    window = closes[-period:]
    mid = sum(window) / period
    variance = sum((v - mid) ** 2 for v in window) / period
    std = variance ** 0.5
    return mid + 2 * std, mid, mid - 2 * std


# ══════════════════════════════════════════════════════════════════════════════
#  Signal scoring
# ══════════════════════════════════════════════════════════════════════════════

def _score_symbol(
    closes: List[float],
) -> Optional[Dict[str, Any]]:
    """Return a signal dict or None if no qualified signal."""
    if len(closes) < _MIN_BARS:
        return None

    last_close = closes[-1]
    if last_close <= 0:
        return None

    votes: Dict[str, float] = {}          # "BUY"/"SELL" → accumulated confidence
    features: Dict[str, Any] = {}

    # ── RSI ───────────────────────────────────────────────────────────────────
    rsi_val = _rsi(closes)
    if rsi_val is not None:
        features["rsi14"] = round(rsi_val, 2)
        if rsi_val < 30:
            conf = min((30 - rsi_val) / 30, 1.0)   # 0→1 as RSI→0
            votes["BUY"]  = votes.get("BUY",  0.0) + conf * 0.40
        elif rsi_val > 70:
            conf = min((rsi_val - 70) / 30, 1.0)
            votes["SELL"] = votes.get("SELL", 0.0) + conf * 0.40

    # ── MACD crossover ────────────────────────────────────────────────────────
    macd_val, signal_val = _macd(closes)
    if macd_val is not None and signal_val is not None:
        features["macd"] = round(macd_val, 4)
        features["macd_signal"] = round(signal_val, 4)
        spread = macd_val - signal_val
        if spread > 0:
            conf = min(abs(spread) / (last_close * 0.005 + 1e-9), 1.0)
            votes["BUY"]  = votes.get("BUY",  0.0) + conf * 0.35
        elif spread < 0:
            conf = min(abs(spread) / (last_close * 0.005 + 1e-9), 1.0)
            votes["SELL"] = votes.get("SELL", 0.0) + conf * 0.35

    # ── Bollinger Bands ───────────────────────────────────────────────────────
    upper, mid, lower = _bollinger(closes)
    if upper is not None and lower is not None and mid is not None:
        features["bb_upper"] = round(upper, 2)
        features["bb_mid"]   = round(mid, 2)
        features["bb_lower"] = round(lower, 2)
        if last_close < lower:
            conf = min((lower - last_close) / (mid - lower + 1e-9), 1.0)
            votes["BUY"]  = votes.get("BUY",  0.0) + conf * 0.25
        elif last_close > upper:
            conf = min((last_close - upper) / (upper - mid + 1e-9), 1.0)
            votes["SELL"] = votes.get("SELL", 0.0) + conf * 0.25

    if not votes:
        return None

    best_dir = max(votes, key=lambda k: votes[k])
    confidence = round(min(votes[best_dir], 1.0), 4)

    if confidence < _CONFIDENCE_MIN:
        return None

    # Conflict check: if the opposing direction has a strong counter-vote, skip
    other_dirs = [d for d in votes if d != best_dir]
    if other_dirs:
        opposing = max(votes[d] for d in other_dirs)
        if opposing >= confidence * 0.75:
            return None

    # Compute entry / target / stop-loss
    entry = round(last_close, 2)
    if best_dir == "BUY":
        target = round(entry * (1 + _BUY_TARGET_PCT), 2)
        sl     = round(entry * (1 - _BUY_SL_PCT), 2)
    else:
        target = round(entry * (1 - _SELL_TARGET_PCT), 2)
        sl     = round(entry * (1 + _SELL_SL_PCT), 2)

    features["close"] = entry
    return {
        "signal_type": best_dir,
        "confidence":  confidence,
        "entry_price": entry,
        "target_price": target,
        "stop_loss":   sl,
        "features":    features,
    }


# ══════════════════════════════════════════════════════════════════════════════
#  DB helpers
# ══════════════════════════════════════════════════════════════════════════════

def _get_db_conn():
    import psycopg2
    from app.core.config import settings
    return psycopg2.connect(
        host=settings.db_host,
        port=settings.db_port,
        dbname=settings.db_name,
        user=settings.db_user,
        password=settings.db_password,
    )


# ══════════════════════════════════════════════════════════════════════════════
#  Celery task
# ══════════════════════════════════════════════════════════════════════════════

@celery_app.task(bind=True, name="app.tasks.signal_generator.generate_signals")
def generate_signals(self):
    """Compute RSI/MACD/Bollinger signals for all active symbols and persist them."""
    conn = _get_db_conn()
    cur  = conn.cursor()

    try:
        # ── 1. Load active symbols ─────────────────────────────────────────────
        cur.execute(
            "SELECT symbol FROM stock_universe WHERE is_active = TRUE ORDER BY market_cap DESC NULLS LAST"
        )
        symbols: List[str] = [row[0] for row in cur.fetchall()]
        total    = len(symbols)
        inserted = 0
        skipped  = 0
        now_ts   = datetime.utcnow()

        logger.info("signal_generator.start", total_symbols=total)

        for idx in range(0, total, _BATCH_SIZE):
            batch = symbols[idx : idx + _BATCH_SIZE]

            for sym in batch:
                # ── Fetch last _LOOKBACK days of OHLCV ────────────────────────
                cur.execute(
                    """
                    SELECT close
                    FROM   ohlcv_daily
                    WHERE  symbol = %s
                    ORDER  BY ts DESC
                    LIMIT  %s
                    """,
                    (sym, _LOOKBACK),
                )
                rows = cur.fetchall()
                if len(rows) < _MIN_BARS:
                    skipped += 1
                    continue

                # Oldest → newest (reversed since we fetched DESC)
                closes = [float(r[0]) for r in reversed(rows)]

                signal = _score_symbol(closes)
                if signal is None:
                    skipped += 1
                    continue

                sig_id = uuid.uuid4()

                # ── Deactivate previous active signals for this symbol ─────────
                cur.execute(
                    "UPDATE signals SET is_active = FALSE WHERE symbol = %s AND is_active = TRUE",
                    (sym,),
                )

                # ── Insert new signal ─────────────────────────────────────────
                cur.execute(
                    """
                    INSERT INTO signals
                        (id, symbol, ts, signal_type, confidence,
                         entry_price, target_price, stop_loss,
                         model_version, features, is_active, created_at)
                    VALUES
                        (%s, %s, %s, %s, %s,
                         %s, %s, %s,
                         %s, %s, TRUE, %s)
                    """,
                    (
                        str(sig_id),
                        sym,
                        now_ts,
                        signal["signal_type"],
                        signal["confidence"],
                        signal["entry_price"],
                        signal["target_price"],
                        signal["stop_loss"],
                        _MODEL_VERSION,
                        json.dumps(signal["features"]),
                        now_ts,
                    ),
                )
                inserted += 1

                # ── Discord alert (best-effort, non-blocking) ──────────────────
                try:
                    from app.services.discord_service import notify_signal_sync
                    notify_signal_sync(
                        symbol=sym,
                        signal_type=signal["signal_type"],
                        confidence=signal["confidence"],
                        entry=signal["entry_price"],
                        target=signal["target_price"],
                        sl=signal["stop_loss"],
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("signal_generator.discord_failed", error=str(exc))

            conn.commit()

        logger.info("signal_generator.done", inserted=inserted, skipped=skipped)
        return {"status": "ok", "inserted": inserted, "skipped": skipped}

    except Exception as exc:
        conn.rollback()
        logger.error("signal_generator.error", error=str(exc))
        raise
    finally:
        cur.close()
        conn.close()
