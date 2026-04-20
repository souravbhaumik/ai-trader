"""Feature Engineering validation + pre-computation task.

Iterates all active symbols, computes technical features from ohlcv_daily,
and reports how many symbols are training-ready.  This is a pre-flight check
before running ML training — it catches data gaps early and gives the admin
confidence that training will succeed.

No new table is written; features are computed on-demand during training.
This task only validates readiness and writes summary stats to task_status.
"""
from __future__ import annotations

import structlog
from sqlalchemy import text

from app.core.database import get_sync_session
from app.tasks.celery_app import celery_app
from app.tasks.task_utils import (
    append_task_log,
    clear_task_logs,
    write_task_status,
    now_iso,
)

logger = structlog.get_logger(__name__)

_TASK_NAME = "feature_engineering"

# Minimum bars needed to compute all features (ADX=14, MACD=35, SMA50=50 + buffer)
_MIN_BARS = 60


def _set_log(message: str, pct: int = 0, level: str = "info") -> None:
    append_task_log(_TASK_NAME, f"[{pct:3d}%] {message}", level=level)
    logger.info("feature_engineering.progress", pct=pct, msg=message)


@celery_app.task(name="app.tasks.feature_engineering.run_feature_engineering", bind=True)
def run_feature_engineering(self):  # noqa: ANN001
    """Validate feature readiness across all active symbols."""
    clear_task_logs(_TASK_NAME)
    write_task_status(_TASK_NAME, "running", "Feature engineering check started.",
                      started_at=now_iso())

    try:
        from app.services.feature_engineer import build_features, FEATURE_NAMES

        _set_log("Fetching active symbols from stock_universe…", pct=2)

        with get_sync_session() as session:
            rows = session.execute(
                text("SELECT symbol FROM stock_universe WHERE is_active = TRUE ORDER BY symbol")
            ).fetchall()

        symbols = [r[0] for r in rows]
        total = len(symbols)
        _set_log(f"{total} active symbols found.", pct=5)

        ready = 0
        insufficient = 0
        errors = 0
        insufficient_list: list[str] = []

        with get_sync_session() as session:
            for i, symbol in enumerate(symbols):
                pct = 5 + int((i / total) * 90)

                try:
                    ohlcv = session.execute(
                        text("""
                            SELECT ts, open, high, low, close, volume
                            FROM   ohlcv_daily
                            WHERE  symbol = :sym
                            ORDER  BY ts ASC
                        """),
                        {"sym": symbol},
                    ).fetchall()

                    if len(ohlcv) < _MIN_BARS:
                        insufficient += 1
                        if len(insufficient_list) < 20:
                            insufficient_list.append(f"{symbol}({len(ohlcv)}bars)")
                        continue

                    closes  = [float(r[4]) for r in ohlcv]
                    highs   = [float(r[2]) for r in ohlcv]
                    lows    = [float(r[3]) for r in ohlcv]
                    volumes = [float(r[5]) for r in ohlcv]
                    features = build_features(symbol, closes, highs, lows, volumes, sentiment_score=0.0)

                    valid_count = sum(
                        1 for k in FEATURE_NAMES
                        if k != "sentiment_score" and features.get(k) is not None
                        and features[k] == features[k]  # not NaN
                    )
                    if valid_count >= len(FEATURE_NAMES) - 1:  # allow sentiment to be 0
                        ready += 1
                    else:
                        insufficient += 1
                        if len(insufficient_list) < 20:
                            insufficient_list.append(f"{symbol}(incomplete_features)")

                except Exception as exc:
                    errors += 1
                    logger.warning("feature_engineering.symbol_error", symbol=symbol, err=str(exc))

                if i % 100 == 0 and i > 0:
                    _set_log(
                        f"Processed {i}/{total} — ready: {ready}, skipped: {insufficient}, errors: {errors}",
                        pct=pct,
                    )

        pct_ready = round(ready / total * 100, 1) if total else 0
        final_msg = (
            f"Feature check complete: {ready}/{total} symbols ready ({pct_ready}%), "
            f"{insufficient} insufficient data, {errors} errors."
        )
        if insufficient_list:
            final_msg += f" Low-data examples: {', '.join(insufficient_list[:5])}…"

        _set_log(final_msg, pct=100)

        summary = {
            "total": total,
            "ready": ready,
            "insufficient": insufficient,
            "errors": errors,
            "pct_ready": pct_ready,
            "features": FEATURE_NAMES,
        }
        write_task_status(_TASK_NAME, "done", final_msg,
                          finished_at=now_iso(), summary=summary)
        logger.info("feature_engineering.done", **summary)

    except Exception as exc:
        msg = f"Feature engineering failed: {exc}"
        _set_log(msg, pct=0, level="error")
        write_task_status(_TASK_NAME, "error", msg, finished_at=now_iso())
        logger.exception("feature_engineering.fatal")
        raise
