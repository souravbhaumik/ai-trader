"""Technical-indicator + ML signal generation task — Phase 2 / Phase 3.

Runs after EOD data ingestion (4:45 PM IST Mon–Fri).  Reads the last
N days of daily OHLCV from ``ohlcv_daily``, computes three indicators,
and upserts confirmed signals into the ``signals`` table.

Phase 3 upgrade
---------------
When an active LightGBM model exists in ``ml_models``, the technical
confidence score is blended with the ML model probability and the
Phase 4 rolling sentiment score from Redis.

Blend weights (configurable via env vars):
    SIGNAL_WEIGHT_TECH      default 0.40  — pure technical indicators
    SIGNAL_WEIGHT_ML        default 0.45  — LightGBM probability
    SIGNAL_WEIGHT_SENTIMENT default 0.15  — Phase 4 FinBERT sentiment

If no ML model is active, falls back to technical-only (Phase 2 behaviour).
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import structlog

from sqlalchemy import text

from app.core.database import get_sync_session
from app.tasks.celery_app import celery_app
from app.tasks.task_utils import (
    append_task_log, clear_task_logs, now_iso, write_task_status,
)

logger = structlog.get_logger(__name__)

_TASK = "signal_generator"

# ── Tunables ──────────────────────────────────────────────────────────────────
_LOOKBACK       = 90    # calendar days fetched per symbol (Phase 3 needs more)
_MIN_BARS       = 28    # minimum valid rows needed (skip thin history)
_CONFIDENCE_MIN = 0.25  # signals below this threshold are discarded
_BATCH_SIZE     = 20    # symbols fetched from DB per loop iteration
_BUY_TARGET_PCT = 0.05  # expected upside for BUY  (5 %)
_BUY_SL_PCT     = 0.03  # stop-loss for BUY         (3 %)
_SELL_TARGET_PCT = 0.05 # expected downside for SELL (5 %)
_SELL_SL_PCT     = 0.03 # stop-loss for SELL         (3 %)
_MODEL_VERSION  = "technical-v1"   # overridden when ML model active

# ── Blend weights (Phase 3) ───────────────────────────────────────────────────
_W_TECH      = float(os.getenv("SIGNAL_WEIGHT_TECH",      "0.40"))
_W_ML        = float(os.getenv("SIGNAL_WEIGHT_ML",        "0.45"))
_W_SENTIMENT = float(os.getenv("SIGNAL_WEIGHT_SENTIMENT", "0.15"))


# ══════════════════════════════════════════════════════════════════════════════
#  Technical indicators — delegated to feature_engineer (single source of truth)
# ══════════════════════════════════════════════════════════════════════════════

import numpy as np

# Indian Standard Time — UTC+5:30 — all timestamps written to DB use IST
from datetime import timezone, timedelta
_IST = timezone(timedelta(hours=5, minutes=30))


def _rsi(closes: List[float], period: int = 14) -> Optional[float]:
    """RSI of the most-recent bar. Delegates to feature_engineer."""
    from app.services.feature_engineer import _rsi as _fe_rsi
    c = np.array(closes, dtype=float)
    val = _fe_rsi(c, period)
    return None if np.isnan(val) else float(val)


def _macd(closes: List[float]) -> Tuple[Optional[float], Optional[float]]:
    """Return (macd_line, signal_line) for the most-recent bar."""
    from app.services.feature_engineer import _ema
    if len(closes) < 35:
        return None, None
    c = np.array(closes, dtype=float)
    e12 = _ema(c, 12)
    e26 = _ema(c, 26)
    macd_line = e12 - e26
    valid_start = 25
    signal = _ema(macd_line[valid_start:], 9)
    if len(signal) == 0 or np.isnan(signal[-1]):
        return None, None
    return float(macd_line[-1]), float(signal[-1])


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
#  Celery task
# ══════════════════════════════════════════════════════════════════════════════

@celery_app.task(bind=True, name="app.tasks.signal_generator.generate_signals")
def generate_signals(self):
    """Compute signals for all active symbols and persist them.

    Phase 3: blends technical indicators with LightGBM ML model probability
    and Phase 4 sentiment score when an active model exists.
    Falls back to technical-only mode otherwise.
    """
    from app.services.ml_loader import predict as ml_predict
    from app.services.feature_engineer import build_features, FEATURE_NAMES

    started = now_iso()
    clear_task_logs(_TASK)
    write_task_status(_TASK, "running", "Signal generation started.", started_at=started)

    # ── Check if an active ML model is loaded ─────────────────────────────────
    ml_available = ml_predict(dict.fromkeys(FEATURE_NAMES, 0.0)) is not None
    append_task_log(_TASK, f"ML model active: {ml_available}")

    # ── Prefetch all sentiment scores from Redis ──────────────────────────────
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
                    import json as _json
                    sym = k.removeprefix("sentiment:")
                    sentiment_cache[sym] = float(_json.loads(v).get("score", 0.0))
    except Exception as exc:
        logger.warning("signal_generator.sentiment_prefetch_failed", err=str(exc))

    with get_sync_session() as session:
        # ── 1. Load active symbols ─────────────────────────────────────────────
        symbols: List[str] = [
            row[0] for row in session.execute(
                text(
                    "SELECT symbol FROM stock_universe"
                    " WHERE is_active = TRUE ORDER BY market_cap DESC NULLS LAST"
                )
            ).fetchall()
        ]
        total    = len(symbols)
        inserted = 0
        skipped  = 0
        # Use IST-aware timestamp so signals in the DB reflect IST wall time
        now_ts   = datetime.now(_IST)

        logger.info("signal_generator.start", total_symbols=total, ml_mode=ml_available)
        append_task_log(_TASK, f"Loaded {total} active symbols. Starting signal computation…")

        for idx in range(0, total, _BATCH_SIZE):
            batch = symbols[idx : idx + _BATCH_SIZE]

            for sym in batch:
                # ── Fetch last _LOOKBACK days of OHLCV ────────────────────────
                rows = session.execute(
                    text("""
                        SELECT close, high, low, volume
                        FROM   ohlcv_daily
                        WHERE  symbol = :symbol
                        ORDER  BY ts DESC
                        LIMIT  :limit
                    """),
                    {"symbol": sym, "limit": _LOOKBACK},
                ).fetchall()
                if len(rows) < _MIN_BARS:
                    skipped += 1
                    continue

                # Oldest → newest
                rows_asc = list(reversed(rows))

                # Lookahead guard: if running during IST market hours, drop
                # the current incomplete bar so we don't score a half-candle.
                import datetime as _dt
                _now_ist = _dt.datetime.now(_IST)
                _market_open = (
                    _now_ist.weekday() < 5           # Mon–Fri
                    and _dt.time(9, 15) <= _now_ist.time() <= _dt.time(15, 30)
                )
                if _market_open and len(rows_asc) > _MIN_BARS:
                    rows_asc = rows_asc[:-1]   # discard today's incomplete candle

                closes  = [float(r[0]) for r in rows_asc]
                highs   = [float(r[1]) for r in rows_asc]
                lows    = [float(r[2]) for r in rows_asc]
                volumes = [float(r[3]) for r in rows_asc]

                # ── Technical signal (always computed) ────────────────────────
                tech_signal = _score_symbol(closes)
                if tech_signal is None:
                    skipped += 1
                    continue

                tech_dir   = tech_signal["signal_type"]   # "BUY" | "SELL"
                tech_conf  = tech_signal["confidence"]    # [0, 1]
                entry      = tech_signal["entry_price"]
                target     = tech_signal["target_price"]
                stop_loss  = tech_signal["stop_loss"]
                features   = tech_signal["features"]
                model_ver  = _MODEL_VERSION

                final_dir  = tech_dir
                final_conf = tech_conf

                # ── Phase 3: blend ML + sentiment ─────────────────────────────
                if ml_available:
                    sentiment = max(-1.0, min(1.0, sentiment_cache.get(sym, 0.0)))
                    feat_vec  = build_features(sym, closes, highs, lows, volumes, sentiment)
                    ml_result = ml_predict(feat_vec)

                    if ml_result:
                        # Always record that ML was used, even when direction is HOLD
                        model_ver = ml_result["version"]

                    if ml_result and ml_result["direction"] != "HOLD":
                        ml_dir   = ml_result["direction"]
                        ml_prob  = ml_result["probability"]   # [0,1] BUY probability
                        ml_score = ml_prob if ml_dir == "BUY" else (1 - ml_prob)

                        # Sentiment contribution: map [-1,1] → [0,1] for BUY polarity
                        sent_score = (sentiment + 1) / 2

                        # Convert tech confidence to direction-aligned score
                        tech_score = tech_conf if tech_dir == "BUY" else (1 - tech_conf)

                        # Weighted blend
                        blended = (
                            _W_TECH      * tech_score  +
                            _W_ML        * ml_score    +
                            _W_SENTIMENT * sent_score
                        )

                        # Direction: majority vote between tech + ML
                        final_dir  = "BUY" if blended >= 0.5 else "SELL"
                        final_conf = abs(blended - 0.5) * 2   # rescale to [0,1]
                        model_ver  = ml_result["version"]

                        features["ml_probability"] = round(ml_prob, 4)
                        features["sentiment_score"] = round(sentiment, 4)
                        features["blend_score"]     = round(blended, 4)

                # ── Phase 5: LSTM anomaly penalty ─────────────────────────────
                # Build bar dicts needed by lstm_service (same rows already fetched)
                try:
                    from app.services.lstm_service import compute_anomaly_score  # noqa: PLC0415
                    bar_dicts = [
                        {"close": float(r[0]), "high": float(r[1]),
                         "low": float(r[2]), "volume": float(r[3])}
                        for r in rows_asc
                    ]
                    anomaly = compute_anomaly_score(sym, bar_dicts)
                    if anomaly:
                        features["anomaly_score"] = round(anomaly["score"], 4)
                        if anomaly["score"] > 0.8:
                            # Scale penalty: 0 at score=0.8, up to 40% at score≥1.8
                            penalty    = min((anomaly["score"] - 0.8) / 1.0 * 0.40, 0.40)
                            final_conf = max(final_conf * (1.0 - penalty), 0.0)
                            features["anomaly_penalty"] = round(penalty, 4)
                except Exception:  # noqa: BLE001
                    pass  # Best-effort anomaly check — never block signal generation

                # ── Phase 3b: Fundamentals score ──────────────────────────────
                # Reads from Redis cache (written daily by fundamentals_ingest)
                # Adds ±10% weight to final confidence via a simple bump.
                try:
                    from app.services.fundamentals_service import (
                        get_fundamentals_from_cache, score_fundamentals,
                    )
                    import redis as _redis_fund
                    from app.core.config import settings as _cfg_fund
                    _rf = _redis_fund.from_url(_cfg_fund.redis_url, decode_responses=True)
                    fund_data = get_fundamentals_from_cache(sym, redis_client=_rf)
                    if fund_data:
                        fund_score = score_fundamentals(fund_data)   # [-1, 1]
                        # Bump of up to ±5 pp toward final direction
                        direction_multiplier = 1.0 if final_dir == "BUY" else -1.0
                        fund_bump = direction_multiplier * fund_score * 0.05
                        final_conf = max(0.0, min(1.0, final_conf + fund_bump))
                        features["fundamentals_score"] = round(fund_score, 4)
                except Exception:  # noqa: BLE001
                    pass  # Cache miss or yfinance error — skip silently

                # ── Phase 6: River ARF online model ───────────────────────────
                # ARF is trained online; it returns None until ≥50 samples seen.
                # When available, its prediction shifts blend score by ±5 pp.
                try:
                    from app.services.river_amf import get_model as _get_arf
                    _arf = _get_arf()
                    # Ensure feat_vec is available (may be missing if ML unavailable)
                    if "feat_vec" not in dir():
                        feat_vec = build_features(sym, closes, highs, lows, volumes,
                                                  sentiment_cache.get(sym, 0.0))
                    arf_pred = _arf.predict_one(feat_vec)      # 1=BUY, 0=SELL, None=not_ready
                    if arf_pred is not None:
                        # ARF agrees → +0.05 confidence; disagrees → −0.05
                        agrees = (arf_pred == 1 and final_dir == "BUY") or \
                                 (arf_pred == 0 and final_dir == "SELL")
                        final_conf = max(0.0, min(1.0, final_conf + (0.05 if agrees else -0.05)))
                        features["arf_prediction"] = int(arf_pred)
                    # Always call learn_one so ARF stays up-to-date
                    _arf.learn_one(feat_vec, 1 if tech_dir == "BUY" else 0)
                except Exception:  # noqa: BLE001
                    pass  # River not installed, or model error — best-effort

                # ── Phase 7: Drift detector confidence penalty ────────────────
                # ADWIN drift detector tracks feature distribution shifts.
                # High drift → reduce confidence by up to 50%.
                try:
                    from app.services.drift_detector import get_drift_detector
                    drift_penalty = get_drift_detector().get_confidence_penalty()   # [0, 0.5]
                    if drift_penalty > 0:
                        final_conf = max(0.0, final_conf * (1.0 - drift_penalty))
                        features["drift_penalty"] = round(drift_penalty, 4)
                except Exception:  # noqa: BLE001
                    pass  # Drift detector unavailable — skip

                # ── Phase 8: Regime multiplier ────────────────────────────────
                # risk_on→×1.1, risk_off→×0.6, neutral→×1.0
                try:
                    from app.services.regime_detector import get_regime_confidence_multiplier
                    import redis as _redis_reg
                    from app.core.config import settings as _cfg_reg
                    _rr = _redis_reg.from_url(_cfg_reg.redis_url, decode_responses=True)
                    _regime = _rr.get("macro:sentiment:regime") or "neutral"
                    _rr.close()
                    regime_mult = get_regime_confidence_multiplier(_regime)
                    final_conf  = max(0.0, min(1.0, final_conf * regime_mult))
                    features["regime"]           = _regime
                    features["regime_multiplier"] = round(regime_mult, 4)
                except Exception:  # noqa: BLE001
                    pass  # Redis unavailable or regime not yet computed — skip

                if final_conf < _CONFIDENCE_MIN:
                    skipped += 1
                    continue

                sig_id = uuid.uuid4()

                # ── Deactivate previous active signals for this symbol ─────────
                session.execute(
                    text("UPDATE signals SET is_active = FALSE WHERE symbol = :symbol AND is_active = TRUE"),
                    {"symbol": sym},
                )

                # ── Insert new signal ─────────────────────────────────────────
                session.execute(
                    text("""
                        INSERT INTO signals
                            (id, symbol, ts, signal_type, confidence,
                             entry_price, target_price, stop_loss,
                             model_version, features, is_active, created_at)
                        VALUES
                            (:id, :symbol, :ts, :signal_type, :confidence,
                             :entry_price, :target_price, :stop_loss,
                             :model_version, :features, TRUE, :created_at)
                    """),
                    {
                        "id":            str(sig_id),
                        "symbol":        sym,
                        "ts":            now_ts,
                        "signal_type":   final_dir,
                        "confidence":    round(final_conf, 4),
                        "entry_price":   entry,
                        "target_price":  target,
                        "stop_loss":     stop_loss,
                        "model_version": model_ver,
                        "features":      json.dumps(features),
                        "created_at":    now_ts,
                    },
                )

                # ── Immediately log predicted prices into signal_outcomes ──────
                # Actual prices (price_1d/3d/5d) are filled in automatically by
                # the evaluation task (5 PM EOD + 8:20 AM morning fill next day).
                # Using WHERE NOT EXISTS so reruns are idempotent.
                session.execute(
                    text("""
                        INSERT INTO signal_outcomes (
                            signal_id, symbol, signal_type, signal_ts,
                            entry_price, target_price, stop_loss, confidence,
                            is_evaluated, created_at, tbl_last_dt
                        )
                        SELECT :id, :symbol, :signal_type, :ts,
                               :entry, :target, :sl, :conf,
                               FALSE, NOW(), NOW()
                        WHERE NOT EXISTS (
                            SELECT 1 FROM signal_outcomes WHERE signal_id = :id
                        )
                    """),
                    {
                        "id":          str(sig_id),
                        "symbol":      sym,
                        "signal_type": final_dir,
                        "ts":          now_ts,
                        "entry":       entry,
                        "target":      target,
                        "sl":          stop_loss,
                        "conf":        round(final_conf, 4),
                    },
                )
                inserted += 1

                # ── Discord alert (best-effort, non-blocking) ──────────────────
                try:
                    from app.services.discord_service import notify_signal_sync
                    notify_signal_sync(
                        symbol=sym,
                        signal_type=final_dir,
                        confidence=final_conf,
                        entry=entry,
                        target=target,
                        sl=stop_loss,
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("signal_generator.discord_failed", err=str(exc))

                # ── Real-time WebSocket push via Redis pub/sub (best-effort) ───
                try:
                    import redis as _redis_sync  # noqa: PLC0415
                    from app.core.config import settings as _cfg  # noqa: PLC0415
                    _r = _redis_sync.from_url(_cfg.redis_url, decode_responses=True)
                    _r.publish("signals:new", json.dumps({
                        "id":            str(sig_id),
                        "symbol":        sym,
                        "ts":            now_ts.isoformat(),
                        "signal_type":   final_dir,
                        "confidence":    round(final_conf, 4),
                        "entry_price":   float(entry) if entry is not None else None,
                        "target_price":  float(target) if target is not None else None,
                        "stop_loss":     float(stop_loss) if stop_loss is not None else None,
                        "model_version": model_ver,
                        "is_active":     True,
                    }))
                    _r.close()
                except Exception as exc:  # noqa: BLE001
                    logger.debug("signal_generator.redis_publish_failed", err=str(exc))

                # ── Paper trade auto-execution (Phase 5, best-effort) ──────────
                try:
                    from app.services.paper_trade_service import auto_paper_trade
                    placed = auto_paper_trade(
                        session,
                        signal_id=str(sig_id),
                        symbol=sym,
                        direction=final_dir,
                        entry_price=entry,
                        target_price=target,
                        stop_loss=stop_loss,
                    )
                    if placed:
                        logger.info("paper_trade.auto_queued", symbol=sym, users=placed)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("paper_trade.auto_failed", err=str(exc))

                # ── Async LLM explanation (low_priority queue, best-effort) ────
                # Only queue for high-confidence BUY/SELL signals; HOLD never explained.
                try:
                    from app.core.config import settings as _cfg  # noqa: PLC0415
                    if (
                        final_dir in ("BUY", "SELL")
                        and final_conf >= _cfg.explainability_confidence_threshold
                        and _cfg.explainability_backend != "disabled"
                    ):
                        from app.tasks.explain_signal import explain_signal  # noqa: PLC0415
                        explain_signal.apply_async(
                            args=[str(sig_id)],
                            queue="low_priority",
                            countdown=5,   # brief delay so DB row is committed first
                        )
                except Exception as exc:  # noqa: BLE001
                    logger.debug("signal_generator.explain_queue_failed", err=str(exc))

            session.commit()   # commits signals + paper trades for the batch
            # Log progress every 5 batches
            batch_num = idx // _BATCH_SIZE + 1
            n_batches = (total + _BATCH_SIZE - 1) // _BATCH_SIZE
            if batch_num % 5 == 0 or batch_num == n_batches:
                write_task_status(
                    _TASK, "running",
                    f"Batch {batch_num}/{n_batches} — {inserted} signals so far…",
                    started_at=started,
                )

        msg = f"Signal generation done — {inserted} signals, {skipped} skipped (low confidence / thin data)."
        logger.info("signal_generator.done", inserted=inserted, skipped=skipped,
                    ml_mode=ml_available)
        write_task_status(
            _TASK, "done", msg,
            started_at=started, finished_at=now_iso(),
            summary={"inserted": inserted, "skipped": skipped, "total_symbols": total, "ml_mode": ml_available},
        )
        return {"status": "ok", "inserted": inserted, "skipped": skipped}
