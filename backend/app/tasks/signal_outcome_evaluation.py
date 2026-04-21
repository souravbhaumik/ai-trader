"""Signal outcome evaluation task — Phase 9.

Runs daily at 5:00 PM IST (after EOD data update) to evaluate past signals
and track their actual performance. This data is used for:
  - Win rate dashboard metrics
  - Model performance monitoring
  - User transparency (showing signal accuracy)

The task:
  1. Finds signals from 1, 3, and 5 days ago that haven't been fully evaluated
  2. Fetches current/EOD prices for those symbols
  3. Computes actual returns and target/stoploss hit status
  4. Inserts/updates records in signal_outcomes table
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional

import structlog
from sqlalchemy import text

from app.core.database import get_sync_session
from app.tasks.celery_app import celery_app
from app.tasks.task_utils import (
    append_task_log, clear_task_logs, now_iso, write_task_status,
)

logger = structlog.get_logger(__name__)

_TASK = "signal_outcome_evaluation"

# Indian Standard Time — UTC+5:30
_IST = timezone(timedelta(hours=5, minutes=30))


def _fetch_price_for_symbol(symbol: str, target_date: datetime, session) -> Optional[float]:
    """Fetch closing price for a symbol on a specific date."""
    result = session.execute(
        text("""
            SELECT close FROM ohlcv_daily
            WHERE symbol = :symbol AND ts::date = :target_date
            LIMIT 1
        """),
        {"symbol": symbol, "target_date": target_date.date()},
    )
    row = result.first()
    return float(row.close) if row else None


def _check_target_stoploss_hit(
    signal_type: str,
    entry_price: float,
    target_price: Optional[float],
    stop_loss: Optional[float],
    highs: List[float],
    lows: List[float],
) -> Dict[str, Any]:
    """Check if target or stoploss was hit within the price range data."""
    hit_target = False
    hit_stoploss = False
    max_gain_pct = 0.0
    max_drawdown_pct = 0.0
    
    for high, low in zip(highs, lows):
        if signal_type == "BUY":
            # For BUY: target hit when high >= target, SL hit when low <= stop_loss
            gain = (high - entry_price) / entry_price * 100
            drawdown = (entry_price - low) / entry_price * 100
            
            max_gain_pct = max(max_gain_pct, gain)
            max_drawdown_pct = max(max_drawdown_pct, drawdown)
            
            if target_price and high >= target_price:
                hit_target = True
            if stop_loss and low <= stop_loss:
                hit_stoploss = True
        else:  # SELL
            # For SELL: target hit when low <= target, SL hit when high >= stop_loss
            gain = (entry_price - low) / entry_price * 100
            drawdown = (high - entry_price) / entry_price * 100
            
            max_gain_pct = max(max_gain_pct, gain)
            max_drawdown_pct = max(max_drawdown_pct, drawdown)
            
            if target_price and low <= target_price:
                hit_target = True
            if stop_loss and high >= stop_loss:
                hit_stoploss = True
    
    return {
        "hit_target": hit_target,
        "hit_stoploss": hit_stoploss,
        "max_gain_pct": round(max_gain_pct, 4),
        "max_drawdown_pct": round(max_drawdown_pct, 4),
    }


def _get_ohlcv_range(symbol: str, start_date: datetime, end_date: datetime, session) -> Dict[str, Any]:
    """Get OHLCV data for a symbol between two dates."""
    result = session.execute(
        text("""
            SELECT ts, open, high, low, close, volume
            FROM ohlcv_daily
            WHERE symbol = :symbol AND ts >= :start_date AND ts <= :end_date
            ORDER BY ts ASC
        """),
        {"symbol": symbol, "start_date": start_date, "end_date": end_date},
    )
    rows = result.fetchall()
    
    return {
        "highs": [float(r.high) for r in rows],
        "lows": [float(r.low) for r in rows],
        "closes": [float(r.close) for r in rows],
        "dates": [r.ts for r in rows],
    }


def _send_outcome_notifications(
    *,
    symbol: str,
    signal_type: str,
    entry_price: float,
    target_price: Optional[float],
    stop_loss: Optional[float],
    hit_target: bool,
    hit_stoploss: bool,
) -> None:
    """Send Discord webhook + Expo push notification when target or SL is hit.

    Called only on the *first* time each flag transitions False → True, so
    users receive exactly one alert per event.
    """
    if hit_target:
        event   = "🎯 Target Hit"
        price   = target_price
        colour  = 0x00C896   # green
    else:
        event   = "🛑 Stop-Loss Hit"
        price   = stop_loss
        colour  = 0xFF4444   # red

    price_str = f"₹{price:,.2f}" if price else "—"
    msg = (
        f"**{event}** | {symbol} | {signal_type}\n"
        f"Entry: ₹{entry_price:,.2f} → {event}: {price_str}"
    )

    # Discord (best-effort)
    try:
        from app.services.discord_service import notify_signal_sync  # noqa: PLC0415
        notify_signal_sync(
            symbol=symbol,
            signal_type=signal_type,
            confidence=0.0,          # not applicable for outcome alert
            entry=entry_price,
            target=target_price,
            sl=stop_loss,
            extra_message=msg,
        )
    except Exception as exc:
        logger.warning("outcome_notification.discord_failed", err=str(exc))

    # Expo push (best-effort — sends to all subscribed tokens)
    try:
        from app.services.push_notification_service import send_push_to_all  # noqa: PLC0415
        send_push_to_all(title=event, body=f"{symbol}: {msg}")
    except Exception as exc:
        logger.warning("outcome_notification.push_failed", err=str(exc))


@celery_app.task(name="app.tasks.signal_outcome_evaluation.evaluate_signal_outcomes")
def evaluate_signal_outcomes() -> str:
    """Evaluate past signals and record their outcomes."""
    clear_task_logs(_TASK)
    append_task_log(_TASK, f"[{now_iso()}] Starting signal outcome evaluation...")
    write_task_status(_TASK, "running", "Evaluating signal outcomes...")

    # Use IST midnight so day boundaries match NSE trading days (UTC+5:30)
    today = datetime.now(_IST).replace(hour=0, minute=0, second=0, microsecond=0)
    
    # Evaluate signals from 1, 3, and 5 trading days ago
    evaluation_windows = [1, 3, 5]
    
    signals_processed = 0
    outcomes_created = 0
    outcomes_updated = 0
    
    try:
        with get_sync_session() as session:
            # Find signals that need evaluation (from last 7 days, not fully evaluated)
            cutoff_date = today - timedelta(days=7)
            
            result = session.execute(
                text("""
                    SELECT s.id, s.symbol, s.ts, s.signal_type, s.confidence,
                           s.entry_price, s.target_price, s.stop_loss
                    FROM signals s
                    LEFT JOIN signal_outcomes so ON so.signal_id = s.id
                    WHERE s.ts >= :cutoff
                      AND s.signal_type IN ('BUY', 'SELL')
                      AND s.entry_price IS NOT NULL
                      AND (so.id IS NULL OR so.is_evaluated = FALSE)
                    ORDER BY s.ts DESC
                    LIMIT 500
                """),
                {"cutoff": cutoff_date},
            )
            signals = result.fetchall()
            
            append_task_log(_TASK, f"[{now_iso()}] Found {len(signals)} signals to evaluate")
            
            for signal in signals:
                signals_processed += 1
                signal_date = signal.ts.replace(hour=0, minute=0, second=0, microsecond=0)
                days_since = (today - signal_date).days
                
                if days_since < 1:
                    continue  # Too early to evaluate
                
                # Get OHLCV data for the period after signal
                end_date = min(today, signal_date + timedelta(days=6))
                ohlcv_data = _get_ohlcv_range(
                    signal.symbol, 
                    signal_date + timedelta(days=1), 
                    end_date, 
                    session
                )
                
                if not ohlcv_data["closes"]:
                    continue  # No data yet
                
                entry_price = float(signal.entry_price)
                
                # Calculate returns at different intervals
                price_1d = ohlcv_data["closes"][0] if len(ohlcv_data["closes"]) >= 1 else None
                price_3d = ohlcv_data["closes"][2] if len(ohlcv_data["closes"]) >= 3 else None
                price_5d = ohlcv_data["closes"][4] if len(ohlcv_data["closes"]) >= 5 else None
                
                def calc_return(price: Optional[float], signal_type: str) -> Optional[float]:
                    if price is None:
                        return None
                    if signal_type == "BUY":
                        return round((price - entry_price) / entry_price * 100, 4)
                    else:  # SELL
                        return round((entry_price - price) / entry_price * 100, 4)
                
                return_1d = calc_return(price_1d, signal.signal_type)
                return_3d = calc_return(price_3d, signal.signal_type)
                return_5d = calc_return(price_5d, signal.signal_type)
                
                # Check target/stoploss
                target_sl_result = _check_target_stoploss_hit(
                    signal.signal_type,
                    entry_price,
                    float(signal.target_price) if signal.target_price else None,
                    float(signal.stop_loss) if signal.stop_loss else None,
                    ohlcv_data["highs"],
                    ohlcv_data["lows"],
                )
                
                # Determine if fully evaluated (5 days passed)
                is_evaluated = days_since >= 5
                
                # Check if outcome already exists (also retrieve prior hit flags for notifications)
                existing = session.execute(
                    text("SELECT id, hit_target, hit_stoploss FROM signal_outcomes WHERE signal_id = :sid"),
                    {"sid": str(signal.id)},
                ).first()
                
                if existing:
                    # Check which outcomes are newly hitting target or SL
                    # so we can send notifications only on the first occurrence
                    prev_hit_target   = bool(existing[1]) if len(existing) > 1 else False
                    prev_hit_stoploss = bool(existing[2]) if len(existing) > 2 else False

                    # Update existing outcome
                    session.execute(
                        text("""
                            UPDATE signal_outcomes SET
                                price_1d = COALESCE(:price_1d, price_1d),
                                price_3d = COALESCE(:price_3d, price_3d),
                                price_5d = COALESCE(:price_5d, price_5d),
                                return_1d_pct = COALESCE(:return_1d, return_1d_pct),
                                return_3d_pct = COALESCE(:return_3d, return_3d_pct),
                                return_5d_pct = COALESCE(:return_5d, return_5d_pct),
                                hit_target = :hit_target,
                                hit_stoploss = :hit_stoploss,
                                max_gain_pct = :max_gain,
                                max_drawdown_pct = :max_dd,
                                is_evaluated = :is_eval,
                                evaluated_at = CASE WHEN :is_eval THEN NOW() ELSE evaluated_at END
                            WHERE signal_id = :sid
                        """),
                        {
                            "price_1d": price_1d,
                            "price_3d": price_3d,
                            "price_5d": price_5d,
                            "return_1d": return_1d,
                            "return_3d": return_3d,
                            "return_5d": return_5d,
                            "hit_target": target_sl_result["hit_target"],
                            "hit_stoploss": target_sl_result["hit_stoploss"],
                            "max_gain": target_sl_result["max_gain_pct"],
                            "max_dd": target_sl_result["max_drawdown_pct"],
                            "is_eval": is_evaluated,
                            "sid": str(signal.id),
                        },
                    )
                    outcomes_updated += 1

                    # ── Notifications: fire only on first occurrence of hit ────────────
                    newly_hit_target   = target_sl_result["hit_target"]   and not prev_hit_target
                    newly_hit_stoploss = target_sl_result["hit_stoploss"] and not prev_hit_stoploss
                    if newly_hit_target or newly_hit_stoploss:
                        _send_outcome_notifications(
                            symbol=signal.symbol,
                            signal_type=signal.signal_type,
                            entry_price=entry_price,
                            target_price=float(signal.target_price) if signal.target_price else None,
                            stop_loss=float(signal.stop_loss) if signal.stop_loss else None,
                            hit_target=newly_hit_target,
                            hit_stoploss=newly_hit_stoploss,
                        )
                else:
                    # Insert new outcome
                    session.execute(
                        text("""
                            INSERT INTO signal_outcomes (
                                signal_id, symbol, signal_type, signal_ts,
                                entry_price, target_price, stop_loss, confidence,
                                price_1d, price_3d, price_5d,
                                return_1d_pct, return_3d_pct, return_5d_pct,
                                hit_target, hit_stoploss,
                                max_gain_pct, max_drawdown_pct,
                                is_evaluated, evaluated_at
                            ) VALUES (
                                :signal_id, :symbol, :signal_type, :signal_ts,
                                :entry_price, :target_price, :stop_loss, :confidence,
                                :price_1d, :price_3d, :price_5d,
                                :return_1d, :return_3d, :return_5d,
                                :hit_target, :hit_stoploss,
                                :max_gain, :max_dd,
                                :is_eval, CASE WHEN :is_eval THEN NOW() ELSE NULL END
                            )
                        """),
                        {
                            "signal_id": str(signal.id),
                            "symbol": signal.symbol,
                            "signal_type": signal.signal_type,
                            "signal_ts": signal.ts,
                            "entry_price": entry_price,
                            "target_price": float(signal.target_price) if signal.target_price else None,
                            "stop_loss": float(signal.stop_loss) if signal.stop_loss else None,
                            "confidence": float(signal.confidence),
                            "price_1d": price_1d,
                            "price_3d": price_3d,
                            "price_5d": price_5d,
                            "return_1d": return_1d,
                            "return_3d": return_3d,
                            "return_5d": return_5d,
                            "hit_target": target_sl_result["hit_target"],
                            "hit_stoploss": target_sl_result["hit_stoploss"],
                            "max_gain": target_sl_result["max_gain_pct"],
                            "max_dd": target_sl_result["max_drawdown_pct"],
                            "is_eval": is_evaluated,
                        },
                    )
                    outcomes_created += 1
                
                session.commit()
            
            summary = {
                "signals_processed": signals_processed,
                "outcomes_created": outcomes_created,
                "outcomes_updated": outcomes_updated,
            }
            
            append_task_log(_TASK, f"[{now_iso()}] Evaluation complete: {summary}")
            write_task_status(_TASK, "completed", "Signal outcomes evaluated", summary=summary)
            
            return f"Processed {signals_processed} signals, created {outcomes_created}, updated {outcomes_updated}"
            
    except Exception as exc:
        logger.exception("Signal outcome evaluation failed")
        append_task_log(_TASK, f"[{now_iso()}] ERROR: {exc}")
        write_task_status(_TASK, "failed", str(exc))
        raise
