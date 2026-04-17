"""Historical backfill task — downloads OHLCV data via yfinance for all
active stocks in the stock_universe and inserts into ohlcv_daily.

Triggered manually from the Admin UI or via CLI:
    celery -A app.tasks.celery_app call app.tasks.backfill.backfill_universe

Progress is stored in Redis so the Admin UI can poll it.
"""
from __future__ import annotations

import os
import time
from datetime import datetime
from typing import Optional

import structlog

from sqlalchemy import text

from app.core.database import get_sync_session
from app.tasks.celery_app import celery_app

logger = structlog.get_logger(__name__)

_PROGRESS_KEY = "backfill:progress"
_BATCH_SIZE   = 10   # symbols per yfinance batch call
_DELAY_SECS   = 2.0  # pause between batches to respect rate limits


def _get_redis():
    import redis
    from app.core.config import settings

    return redis.from_url(settings.redis_url, decode_responses=True)


def _set_progress(r, pct: int, message: str, status: str = "running") -> None:
    import json
    r.setex(
        _PROGRESS_KEY,
        3600,  # 1-hour TTL
        json.dumps({"pct": pct, "message": message, "status": status, "ts": datetime.utcnow().isoformat()}),
    )


@celery_app.task(bind=True, name="app.tasks.backfill.backfill_universe")
def backfill_universe(self, period: str = "2y", force: bool = False):
    """Download historical daily OHLCV for all active stocks via yfinance.

    Args:
        period: yfinance period string (1y, 2y, 5y)
        force:  If True, re-download even if data already exists.
    """
    import yfinance as yf
    import pandas as pd

    r = _get_redis()

    try:
        with get_sync_session() as session:
            _set_progress(r, 0, "Loading stock universe from DB...", "running")

            all_symbols = [
                row[0] for row in session.execute(
                    text(
                        "SELECT symbol FROM stock_universe"
                        " WHERE is_active = TRUE ORDER BY market_cap DESC NULLS LAST"
                    )
                ).fetchall()
            ]
            total = len(all_symbols)

            if total == 0:
                _set_progress(r, 100, "No symbols found. Run populate_universe first.", "error")
                return {"status": "error", "message": "Empty universe"}

            logger.info("backfill_start", total=total, period=period)
            _set_progress(r, 1, f"Starting backfill for {total} symbols ({period})...", "running")

            inserted = 0
            skipped  = 0
            errors   = 0

            for batch_start in range(0, total, _BATCH_SIZE):
                batch = all_symbols[batch_start: batch_start + _BATCH_SIZE]
                pct   = int((batch_start / total) * 95)
                msg   = f"Downloading {batch_start + 1}–{min(batch_start + _BATCH_SIZE, total)} / {total}..."
                _set_progress(r, pct, msg, "running")

                try:
                    data = yf.download(
                        batch,
                        period=period,
                        interval="1d",
                        progress=False,
                        auto_adjust=True,
                        threads=True,
                    )
                    if data is None or data.empty:
                        skipped += len(batch)
                        continue

                    # ── Normalise yfinance output ─────────────────────────────
                    if not isinstance(data.columns, pd.MultiIndex):
                        if len(batch) == 1:
                            sym_name = batch[0]
                            data = pd.concat({sym_name: data}, axis=1).swaplevel(axis=1)
                            data.columns = pd.MultiIndex.from_tuples(
                                [(pt, sym_name) for pt in data.columns.get_level_values(0)]
                            )
                        else:
                            logger.warning(
                                "backfill_batch_flat_result_skipped",
                                batch_preview=batch[:3],
                                reason="yfinance returned non-MultiIndex for multi-sym batch",
                            )
                            skipped += len(batch)
                            continue

                    close_df  = data["Close"]
                    open_df   = data["Open"]
                    high_df   = data["High"]
                    low_df    = data["Low"]
                    volume_df = data["Volume"]

                    rows: list[dict] = []

                    for sym in batch:
                        try:
                            if sym not in close_df.columns:
                                skipped += 1
                                continue
                            c = close_df[sym]; o = open_df[sym]; h = high_df[sym]
                            lo = low_df[sym]; v = volume_df[sym]

                            for ts, close_val in c.dropna().items():
                                rows.append({
                                    "symbol": sym,
                                    "ts":     ts.to_pydatetime().replace(tzinfo=None),
                                    "open":   float(o.get(ts, close_val)),
                                    "high":   float(h.get(ts, close_val)),
                                    "low":    float(lo.get(ts, close_val)),
                                    "close":  float(close_val),
                                    "volume": int(v.get(ts, 0) or 0),
                                    "source": "yfinance",
                                })
                            inserted += 1
                        except Exception as e:
                            logger.warning("backfill_symbol_error", sym=sym, error=str(e))
                            errors += 1

                    if rows:
                        session.execute(
                            text("""
                                INSERT INTO ohlcv_daily
                                    (symbol, ts, open, high, low, close, volume, source)
                                VALUES
                                    (:symbol, :ts, :open, :high, :low, :close, :volume, :source)
                                ON CONFLICT (symbol, ts) DO UPDATE SET
                                    open   = EXCLUDED.open,
                                    high   = EXCLUDED.high,
                                    low    = EXCLUDED.low,
                                    close  = EXCLUDED.close,
                                    volume = EXCLUDED.volume,
                                    source = EXCLUDED.source
                            """),
                            rows,
                        )
                        session.commit()

                except Exception as e:
                    logger.error("backfill_batch_error", batch=batch[:3], error=str(e))
                    errors += len(batch)

                time.sleep(_DELAY_SECS)

            final_msg = (
                f"Backfill complete. {inserted} symbols downloaded, "
                f"{skipped} skipped, {errors} errors."
            )
            _set_progress(r, 100, final_msg, "done")
            logger.info("backfill_done", inserted=inserted, skipped=skipped, errors=errors)
            return {"status": "done", "inserted": inserted, "skipped": skipped, "errors": errors}

    except Exception as e:
        _set_progress(r, 0, f"Backfill failed: {e}", "error")
        logger.error("backfill_failed", error=str(e))
        raise
