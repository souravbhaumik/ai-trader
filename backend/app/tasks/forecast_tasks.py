"""Forecast persistence & self-evaluation Celery tasks.

Two tasks:

1. persist_daily_forecasts()
   Runs daily at 16:00 IST (30 min after NSE close).
   Fetches the Nifty 50 / active symbols, runs PatchTST → TFT for each,
   and writes one row per symbol into forecast_history.
   Skips symbols where a forecast for today already exists (idempotent).

2. evaluate_forecast_accuracy()
   Runs daily at 06:30 IST (before market open).
   Finds forecast_history rows where:
     - is_evaluated = FALSE
     - forecast_date + horizon_days <= yesterday (actuals are available)
   Fetches the actual EOD close prices from ohlcv_daily and computes:
     - RMSE  (root mean squared error of predicted vs actual price)
     - MAE   (mean absolute error)
     - Directional accuracy (fraction of days where predicted direction matches)
   Updates the row with actuals + metrics and sets is_evaluated = TRUE.
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timedelta, timezone
from typing import Optional

import structlog
from sqlalchemy import text

from app.core.database import get_sync_session
from app.tasks.celery_app import celery_app
from app.tasks.task_utils import (
    append_task_log, clear_task_logs, now_iso, write_task_status,
)

logger = structlog.get_logger(__name__)

_TASK_PERSIST  = "forecast_persist"
_TASK_EVALUATE = "forecast_evaluate"

# IST timezone
_IST = timezone(timedelta(hours=5, minutes=30))


# ══════════════════════════════════════════════════════════════════════════════
#  Helper: fetch OHLCV bars (sync, for Celery)
# ══════════════════════════════════════════════════════════════════════════════

def _fetch_bars_sync(session, symbol: str, limit: int = 130) -> list[dict]:
    """Fetch the latest `limit` daily OHLCV bars for a symbol (oldest → newest)."""
    rows = session.execute(
        text("""
            SELECT close, high, low, volume
            FROM   ohlcv_daily
            WHERE  symbol = :sym
            ORDER  BY ts DESC
            LIMIT  :lim
        """),
        {"sym": symbol, "lim": limit},
    ).fetchall()
    # DB returns newest-first; reverse to oldest-first for the model
    return [
        {"close": float(r[0]), "high": float(r[1]), "low": float(r[2]), "volume": float(r[3])}
        for r in reversed(rows)
    ]


def _run_forecast(symbol: str, bars: list[dict]) -> Optional[dict]:
    """Run PatchTST → TFT fallback.  Returns raw service result or None."""
    try:
        from app.services.patchtst_service import forecast as patchtst_forecast  # noqa: PLC0415
        result = patchtst_forecast(symbol, bars)
        if result:
            result["_model_type"] = "patchtst"
            return result
    except Exception as exc:
        logger.warning("forecast_persist.patchtst_failed", symbol=symbol, err=str(exc))

    try:
        from app.services.tft_service import forecast as tft_forecast  # noqa: PLC0415
        result = tft_forecast(symbol, bars)
        if result:
            result["_model_type"] = "tft"
            return result
    except Exception as exc:
        logger.warning("forecast_persist.tft_failed", symbol=symbol, err=str(exc))

    return None


# ══════════════════════════════════════════════════════════════════════════════
#  Task 1: persist_daily_forecasts
# ══════════════════════════════════════════════════════════════════════════════

@celery_app.task(name="app.tasks.forecast_tasks.persist_daily_forecasts")
def persist_daily_forecasts():
    """Persist today's 5-day price forecasts for all active symbols.

    Runs at 16:00 IST — after NSE close so today's EOD bar is available.
    Idempotent: skips symbols already forecasted today (UNIQUE constraint).
    """
    started = now_iso()
    clear_task_logs(_TASK_PERSIST)
    write_task_status(_TASK_PERSIST, "running", "Forecast persistence started.", started_at=started)

    today_ist = datetime.now(_IST).date()

    with get_sync_session() as session:
        # Load all active symbols ordered by market cap
        symbols = [
            r[0] for r in session.execute(
                text(
                    "SELECT symbol FROM stock_universe"
                    " WHERE is_active = TRUE ORDER BY market_cap DESC NULLS LAST"
                )
            ).fetchall()
        ]

    total     = len(symbols)
    saved     = 0
    skipped   = 0
    no_model  = 0
    no_data   = 0

    append_task_log(_TASK_PERSIST, f"Running forecasts for {total} symbols (date={today_ist})…")

    for symbol in symbols:
        try:
            with get_sync_session() as session:
                # Idempotency: skip if already persisted for today
                exists = session.execute(
                    text("""
                        SELECT 1 FROM forecast_history
                        WHERE symbol = :sym AND forecast_date = :dt
                        LIMIT 1
                    """),
                    {"sym": symbol, "dt": today_ist},
                ).fetchone()
                if exists:
                    skipped += 1
                    continue

                bars = _fetch_bars_sync(session, symbol, limit=130)

            if len(bars) < 82:
                no_data += 1
                logger.debug("forecast_persist.insufficient_bars", symbol=symbol, bars=len(bars))
                continue

            result = _run_forecast(symbol, bars)
            if result is None:
                no_model += 1
                continue

            predicted_prices = result.get("forecast") or result.get("prices", [])
            base_price       = result.get("base_price", bars[-1]["close"])
            model_version    = result.get("model_version", result.get("version", "unknown"))
            model_type       = result.get("_model_type", "patchtst")
            horizon_days     = len(predicted_prices)

            with get_sync_session() as session:
                session.execute(
                    text("""
                        INSERT INTO forecast_history
                            (symbol, model_version, model_type, forecast_date,
                             base_price, horizon_days, predicted_prices)
                        VALUES
                            (:sym, :ver, :mtype, :dt,
                             :base, :horizon, :prices::jsonb)
                        ON CONFLICT (symbol, model_version, forecast_date)
                        DO NOTHING
                    """),
                    {
                        "sym":     symbol,
                        "ver":     model_version,
                        "mtype":   model_type,
                        "dt":      today_ist,
                        "base":    round(base_price, 4),
                        "horizon": horizon_days,
                        "prices":  json.dumps(predicted_prices),
                    },
                )
                session.commit()
            saved += 1

        except Exception as exc:
            logger.warning("forecast_persist.symbol_failed", symbol=symbol, err=str(exc))

    msg = (
        f"Forecast persistence done: {saved} saved, {skipped} already existed, "
        f"{no_data} insufficient data, {no_model} no model."
    )
    append_task_log(_TASK_PERSIST, msg)
    write_task_status(
        _TASK_PERSIST, "done", msg,
        started_at=started, finished_at=now_iso(),
        summary={"saved": saved, "skipped": skipped, "no_data": no_data, "no_model": no_model},
    )
    logger.info("forecast_persist.done", saved=saved, skipped=skipped, total=total)
    return {"status": "done", "saved": saved, "skipped": skipped}


# ══════════════════════════════════════════════════════════════════════════════
#  Accuracy metrics
# ══════════════════════════════════════════════════════════════════════════════

def _compute_metrics(
    predicted: list[float],
    actual: list[float],
    base_price: float,
) -> dict:
    """Compute RMSE, MAE, and directional accuracy.

    Directional accuracy: for each day i, did the forecast correctly call
    the direction of price change from base_price (or previous actual)?
    """
    n = min(len(predicted), len(actual))
    if n == 0:
        return {"rmse": None, "mae": None, "directional_acc": None}

    p = predicted[:n]
    a = actual[:n]

    mse  = sum((pi - ai) ** 2 for pi, ai in zip(p, a)) / n
    rmse = math.sqrt(mse)
    mae  = sum(abs(pi - ai) for pi, ai in zip(p, a)) / n

    # Directional accuracy: compare direction of change from prior close
    correct = 0
    prev_p  = base_price
    prev_a  = base_price
    for pi, ai in zip(p, a):
        pred_dir   = 1 if pi >= prev_p else -1
        actual_dir = 1 if ai >= prev_a else -1
        if pred_dir == actual_dir:
            correct += 1
        prev_p = pi
        prev_a = ai

    return {
        "rmse":            round(rmse, 6),
        "mae":             round(mae, 6),
        "directional_acc": round(correct / n, 4),
    }


# ══════════════════════════════════════════════════════════════════════════════
#  Task 2: evaluate_forecast_accuracy
# ══════════════════════════════════════════════════════════════════════════════

@celery_app.task(name="app.tasks.forecast_tasks.evaluate_forecast_accuracy")
def evaluate_forecast_accuracy():
    """Fill actual prices and compute RMSE/MAE for matured forecasts.

    Runs at 06:30 IST. A forecast row is 'mature' when:
        forecast_date + horizon_days <= yesterday (IST)
    At that point all actual closing prices are available in ohlcv_daily.
    """
    started   = now_iso()
    clear_task_logs(_TASK_EVALUATE)
    write_task_status(_TASK_EVALUATE, "running", "Forecast evaluation started.", started_at=started)

    yesterday_ist = (datetime.now(_IST) - timedelta(days=1)).date()

    with get_sync_session() as session:
        # Find all un-evaluated rows whose full horizon has passed
        pending = session.execute(
            text("""
                SELECT id, symbol, model_version, forecast_date,
                       base_price, horizon_days, predicted_prices
                FROM   forecast_history
                WHERE  is_evaluated = FALSE
                  AND  forecast_date + horizon_days * INTERVAL '1 day' <= :cutoff
                ORDER  BY forecast_date ASC
                LIMIT  500
            """),
            {"cutoff": yesterday_ist},
        ).fetchall()

    total     = len(pending)
    evaluated = 0
    skipped   = 0

    append_task_log(_TASK_EVALUATE, f"Found {total} matured forecasts to evaluate.")

    for row in pending:
        row_id, symbol, model_version, forecast_date, base_price, horizon_days, pred_json = row

        try:
            predicted = json.loads(pred_json) if isinstance(pred_json, str) else (pred_json or [])
            if not predicted:
                skipped += 1
                continue

            horizon = len(predicted)
            # Fetch actual closing prices for the forecast window
            window_start = forecast_date + timedelta(days=1)
            window_end   = forecast_date + timedelta(days=horizon + 2)  # +2 buffer for weekends

            with get_sync_session() as session:
                actual_rows = session.execute(
                    text("""
                        SELECT close
                        FROM   ohlcv_daily
                        WHERE  symbol = :sym
                          AND  ts::date >= :start
                          AND  ts::date <= :end
                        ORDER  BY ts ASC
                        LIMIT  :lim
                    """),
                    {
                        "sym":   symbol,
                        "start": window_start,
                        "end":   window_end,
                        "lim":   horizon + 2,
                    },
                ).fetchall()

            actual = [float(r[0]) for r in actual_rows[:horizon]]

            if len(actual) < horizon:
                # Not enough actuals yet (missing EOD data for some days — skip for now)
                skipped += 1
                logger.debug(
                    "forecast_eval.insufficient_actuals",
                    symbol=symbol, forecast_date=str(forecast_date),
                    have=len(actual), need=horizon,
                )
                continue

            metrics = _compute_metrics(predicted, actual, float(base_price))
            now_ist = datetime.now(_IST)

            with get_sync_session() as session:
                session.execute(
                    text("""
                        UPDATE forecast_history
                        SET    actual_prices   = :actual::jsonb,
                               rmse            = :rmse,
                               mae             = :mae,
                               directional_acc = :dir_acc,
                               is_evaluated    = TRUE,
                               evaluated_at    = :now
                        WHERE  id            = :id
                          AND  forecast_date = :fdate
                    """),
                    {
                        "actual":  json.dumps(actual),
                        "rmse":    metrics["rmse"],
                        "mae":     metrics["mae"],
                        "dir_acc": metrics["directional_acc"],
                        "now":     now_ist,
                        "id":      str(row_id),
                        "fdate":   forecast_date,
                    },
                )
                session.commit()

            logger.info(
                "forecast_eval.evaluated",
                symbol=symbol, forecast_date=str(forecast_date),
                rmse=metrics["rmse"], mae=metrics["mae"],
                directional_acc=metrics["directional_acc"],
            )
            evaluated += 1

        except Exception as exc:
            logger.warning(
                "forecast_eval.symbol_failed",
                symbol=symbol, forecast_date=str(forecast_date), err=str(exc),
            )

    msg = f"Forecast evaluation done: {evaluated}/{total} evaluated, {skipped} skipped."
    append_task_log(_TASK_EVALUATE, msg)
    write_task_status(
        _TASK_EVALUATE, "done", msg,
        started_at=started, finished_at=now_iso(),
        summary={"evaluated": evaluated, "skipped": skipped, "total": total},
    )
    logger.info("forecast_eval.done", evaluated=evaluated, skipped=skipped, total=total)
    return {"status": "done", "evaluated": evaluated, "skipped": skipped}
