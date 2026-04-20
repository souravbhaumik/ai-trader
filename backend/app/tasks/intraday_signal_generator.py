"""Intraday signal generator — Phase 9.

Runs at 9:30 AM, 11:00 AM, and 1:00 PM IST on market days.

Strategy:
  1. Load the last 90 days of daily OHLCV from ohlcv_daily (for technical history).
  2. Aggregate today's 15-min candles from ohlcv_intraday into a single
     "live today" bar (first-candle open, latest close, intraday high/low,
     cumulative volume). Append this as the most-recent bar.
  3. Run the exact same signal scoring logic as signal_generator.py.
  4. Upsert signals tagged with model_version "intraday-vX".

This ensures intraday signals get progressively more data as the day progresses:
  9:30 run  → 1-2 intraday candles (opening direction)
  11:00 run → 7-8 candles (morning trend confirmed)
  13:00 run → 15 candles  (half-day trend)
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import structlog
from sqlalchemy import text

from app.core.database import get_sync_session
from app.tasks.celery_app import celery_app
from app.tasks.task_utils import (
    append_task_log, clear_task_logs, now_iso, write_task_status,
)

logger = structlog.get_logger(__name__)
_TASK = "intraday_signal_generator"

# ── Tunables (same as EOD generator) ──────────────────────────────────────────
_LOOKBACK       = 90
_MIN_BARS       = 28
_CONFIDENCE_MIN = 0.25
_BATCH_SIZE     = 20
_BUY_TARGET_PCT  = 0.03   # tighter targets intraday
_BUY_SL_PCT      = 0.015
_SELL_TARGET_PCT = 0.03
_SELL_SL_PCT     = 0.015
_MODEL_VERSION   = "intraday-v1"

_W_TECH      = float(os.getenv("SIGNAL_WEIGHT_TECH",      "0.40"))
_W_ML        = float(os.getenv("SIGNAL_WEIGHT_ML",        "0.45"))
_W_SENTIMENT = float(os.getenv("SIGNAL_WEIGHT_SENTIMENT", "0.15"))


def _rsi(closes: List[float], period: int = 14) -> Optional[float]:
    from app.services.feature_engineer import _rsi as _fe_rsi
    c = np.array(closes, dtype=float)
    val = _fe_rsi(c, period)
    return None if np.isnan(val) else float(val)


def _macd(closes: List[float]) -> Tuple[Optional[float], Optional[float]]:
    from app.services.feature_engineer import _ema
    if len(closes) < 35:
        return None, None
    c = np.array(closes, dtype=float)
    e12 = _ema(c, 12)
    e26 = _ema(c, 26)
    macd_line = e12 - e26
    signal = _ema(macd_line[25:], 9)
    if len(signal) == 0 or np.isnan(signal[-1]):
        return None, None
    return float(macd_line[-1]), float(signal[-1])


def _bollinger(closes: List[float], period: int = 20) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    from app.services.feature_engineer import _ema
    if len(closes) < period:
        return None, None, None
    c = np.array(closes[-period:], dtype=float)
    mid  = float(np.mean(c))
    std  = float(np.std(c, ddof=1))
    return mid - 2 * std, mid, mid + 2 * std


def _score_symbol(closes: List[float]) -> Optional[Dict]:
    """Score a symbol using RSI + MACD + Bollinger. Returns None if inconclusive."""
    rsi = _rsi(closes)
    if rsi is None:
        return None

    macd_line, macd_signal = _macd(closes)
    bb_lower, bb_mid, bb_upper = _bollinger(closes)
    last_price = closes[-1]

    buy_score  = 0.0
    sell_score = 0.0

    # RSI
    if rsi < 35:
        buy_score  += 0.35
    elif rsi > 65:
        sell_score += 0.35

    # MACD crossover
    if macd_line is not None and macd_signal is not None:
        if macd_line > macd_signal:
            buy_score  += 0.35
        elif macd_line < macd_signal:
            sell_score += 0.35

    # Bollinger bands
    if bb_lower is not None and bb_upper is not None:
        if last_price < bb_lower:
            buy_score  += 0.30
        elif last_price > bb_upper:
            sell_score += 0.30

    max_score = max(buy_score, sell_score)
    if max_score < _CONFIDENCE_MIN:
        return None

    signal_type = "BUY" if buy_score >= sell_score else "SELL"
    confidence  = round(max_score, 4)

    if signal_type == "BUY":
        target = round(last_price * (1 + _BUY_TARGET_PCT),  2)
        sl     = round(last_price * (1 - _BUY_SL_PCT),     2)
    else:
        target = round(last_price * (1 - _SELL_TARGET_PCT), 2)
        sl     = round(last_price * (1 + _SELL_SL_PCT),     2)

    return {
        "signal_type": signal_type,
        "confidence":  confidence,
        "entry_price": round(last_price, 2),
        "target_price": target,
        "stop_loss":    sl,
        "features": {
            "rsi": round(rsi, 2),
            "macd_line":   round(macd_line, 4) if macd_line else None,
            "macd_signal": round(macd_signal, 4) if macd_signal else None,
            "bb_lower":    round(bb_lower, 2) if bb_lower else None,
            "bb_upper":    round(bb_upper, 2) if bb_upper else None,
            "source":      "intraday",
        },
    }


def _build_intraday_bar(session, symbol: str) -> Optional[dict]:
    """Aggregate today's 15-min candles into one bar. Returns None if no data."""
    today_start_ist = (
        datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    ).replace(hour=9, minute=0, second=0, microsecond=0)
    today_start_utc = today_start_ist.astimezone(timezone.utc).replace(tzinfo=None)

    row = session.execute(
        text("""
            SELECT
                FIRST(open,  ts) AS day_open,
                MAX(high)        AS day_high,
                MIN(low)         AS day_low,
                LAST(close, ts)  AS day_close,
                SUM(volume)      AS day_volume
            FROM ohlcv_intraday
            WHERE symbol = :sym
              AND ts >= :today
              AND interval = '15m'
        """),
        {"sym": symbol, "today": today_start_utc},
    ).first()

    if not row or not row.day_close:
        return None

    return {
        "close":  float(row.day_close),
        "high":   float(row.day_high),
        "low":    float(row.day_low),
        "volume": int(row.day_volume or 0),
    }


@celery_app.task(name="app.tasks.intraday_signal_generator.generate_intraday_signals")
def generate_intraday_signals():
    """Generate intraday signals using daily history + today's live candles."""
    started = now_iso()
    clear_task_logs(_TASK)
    write_task_status(_TASK, "running", "Intraday signal generation started.", started_at=started)

    from app.services.ml_loader import predict as ml_predict
    from app.services.feature_engineer import build_features, FEATURE_NAMES

    ml_available = ml_predict(dict.fromkeys(FEATURE_NAMES, 0.0)) is not None
    append_task_log(_TASK, f"ML model active: {ml_available}")

    # Prefetch sentiment from Redis
    sentiment_cache: Dict[str, float] = {}
    try:
        import redis as _redis
        from app.core.config import settings
        r = _redis.from_url(settings.redis_url, decode_responses=True)
        keys = r.keys("sentiment:*")
        if keys:
            vals = r.mget(keys)
            for k, v in zip(keys, vals):
                if v:
                    sym = k.removeprefix("sentiment:")
                    sentiment_cache[sym] = float(json.loads(v).get("score", 0.0))
    except Exception as exc:
        logger.warning("intraday_signal.sentiment_prefetch_failed", err=str(exc))

    with get_sync_session() as session:
        symbols: List[str] = [
            row[0] for row in session.execute(
                text("SELECT symbol FROM stock_universe WHERE is_active = TRUE ORDER BY market_cap DESC NULLS LAST")
            ).fetchall()
        ]
        total    = len(symbols)
        inserted = 0
        skipped  = 0
        no_intraday = 0
        now_ts   = datetime.utcnow()

        append_task_log(_TASK, f"Loaded {total} symbols. Time: {now_ts.strftime('%H:%M UTC')}")

        for idx in range(0, total, _BATCH_SIZE):
            batch = symbols[idx: idx + _BATCH_SIZE]

            for sym in batch:
                # 1. Load daily history (oldest → newest)
                daily_rows = session.execute(
                    text("""
                        SELECT close, high, low, volume
                        FROM   ohlcv_daily
                        WHERE  symbol = :symbol
                        ORDER  BY ts DESC
                        LIMIT  :limit
                    """),
                    {"symbol": sym, "limit": _LOOKBACK},
                ).fetchall()

                if len(daily_rows) < _MIN_BARS:
                    skipped += 1
                    continue

                rows_asc = list(reversed(daily_rows))

                # 2. Get today's aggregated intraday bar (may be None if market just opened)
                intraday_bar = _build_intraday_bar(session, sym)
                if intraday_bar is None:
                    no_intraday += 1
                    # Still generate signal from daily data alone
                else:
                    # Replace last bar with live intraday data
                    rows_asc[-1] = (
                        intraday_bar["close"],
                        intraday_bar["high"],
                        intraday_bar["low"],
                        intraday_bar["volume"],
                    )

                closes  = [float(r[0]) for r in rows_asc]
                highs   = [float(r[1]) for r in rows_asc]
                lows    = [float(r[2]) for r in rows_asc]
                volumes = [float(r[3]) for r in rows_asc]

                tech_signal = _score_symbol(closes)
                if tech_signal is None:
                    skipped += 1
                    continue

                tech_dir  = tech_signal["signal_type"]
                tech_conf = tech_signal["confidence"]
                entry     = tech_signal["entry_price"]
                target    = tech_signal["target_price"]
                stop_loss = tech_signal["stop_loss"]
                features  = tech_signal["features"]
                model_ver = _MODEL_VERSION

                final_dir  = tech_dir
                final_conf = tech_conf

                if ml_available:
                    sentiment = max(-1.0, min(1.0, sentiment_cache.get(sym, 0.0)))
                    feat_vec  = build_features(sym, closes, highs, lows, volumes, sentiment)
                    ml_result = ml_predict(feat_vec)

                    if ml_result and ml_result["direction"] != "HOLD":
                        ml_dir   = ml_result["direction"]
                        ml_prob  = ml_result["probability"]
                        ml_score = ml_prob if ml_dir == "BUY" else (1 - ml_prob)
                        sent_score = (sentiment + 1) / 2
                        tech_score = tech_conf if tech_dir == "BUY" else (1 - tech_conf)
                        blended = _W_TECH * tech_score + _W_ML * ml_score + _W_SENTIMENT * sent_score
                        final_dir  = "BUY" if blended >= 0.5 else "SELL"
                        final_conf = abs(blended - 0.5) * 2
                        model_ver  = ml_result["version"]
                        features["ml_probability"]  = round(ml_prob, 4)
                        features["sentiment_score"] = round(sentiment, 4)
                        features["blend_score"]     = round(blended, 4)

                if final_conf < _CONFIDENCE_MIN:
                    skipped += 1
                    continue

                # Upsert intraday signal (separate from daily — tagged intraday)
                signal_id = uuid.uuid4()
                session.execute(
                    text("""
                        INSERT INTO signals
                            (id, symbol, ts, signal_type, confidence,
                             entry_price, target_price, stop_loss,
                             model_version, is_active, features)
                        VALUES
                            (:id, :sym, :ts, :st, :conf,
                             :entry, :target, :sl,
                             :mv, TRUE, :feat)
                        ON CONFLICT (symbol, ts, signal_type) DO UPDATE SET
                            confidence    = EXCLUDED.confidence,
                            entry_price   = EXCLUDED.entry_price,
                            target_price  = EXCLUDED.target_price,
                            stop_loss     = EXCLUDED.stop_loss,
                            model_version = EXCLUDED.model_version,
                            features      = EXCLUDED.features
                    """),
                    {
                        "id":    str(signal_id),
                        "sym":   sym,
                        "ts":    now_ts,
                        "st":    final_dir,
                        "conf":  round(final_conf, 4),
                        "entry": entry,
                        "target": target,
                        "sl":    stop_loss,
                        "mv":    model_ver,
                        "feat":  json.dumps(features),
                    },
                )
                inserted += 1

        session.commit()

    msg = (
        f"Intraday signals: {inserted} upserted, {skipped} skipped, "
        f"{no_intraday} had no intraday data yet."
    )
    append_task_log(_TASK, msg)
    write_task_status(
        _TASK, "done", msg,
        started_at=started, finished_at=now_iso(),
        summary={"inserted": inserted, "skipped": skipped, "no_intraday": no_intraday},
    )
    logger.info("intraday_signal_generator.done",
                inserted=inserted, skipped=skipped, no_intraday=no_intraday)
    return {"status": "done", "inserted": inserted}
