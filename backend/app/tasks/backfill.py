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

_PROGRESS_KEY  = "backfill:progress"
_DELAY_SECS    = 3.0   # seconds between individual downloads
_RETRY_DELAY   = 60.0  # seconds to wait after a rate-limit hit
_MAX_RETRIES   = 3     # retries per symbol on rate-limit


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

    r = _get_redis()

    def _download_sym(sym: str) -> list[dict]:
        """Download one symbol with .NS suffix, retry on rate limit."""
        ticker_str = f"{sym}.NS"
        for attempt in range(_MAX_RETRIES):
            try:
                df = yf.Ticker(ticker_str).history(period=period, auto_adjust=True)
                if df is None or df.empty:
                    return []
                rows = []
                for ts, row in df.iterrows():
                    rows.append({
                        "symbol": sym,
                        "ts":     ts.to_pydatetime().replace(tzinfo=None),
                        "open":   float(row["Open"]),
                        "high":   float(row["High"]),
                        "low":    float(row["Low"]),
                        "close":  float(row["Close"]),
                        "volume": int(row.get("Volume") or 0),
                        "source": "yfinance",
                    })
                return rows
            except Exception as e:
                err = str(e)
                if "RateLimit" in err or "Too Many Requests" in err:
                    if attempt < _MAX_RETRIES - 1:
                        logger.warning("backfill_rate_limit_retry", sym=sym, attempt=attempt + 1)
                        time.sleep(_RETRY_DELAY)
                    else:
                        raise
                else:
                    raise
        return []

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

            for idx, sym in enumerate(all_symbols):
                pct = int((idx / total) * 95)
                if idx % 50 == 0:
                    _set_progress(r, pct, f"Downloading {idx + 1}/{total}…", "running")

                try:
                    rows = _download_sym(sym)
                    if not rows:
                        skipped += 1
                        logger.debug("backfill_no_data", sym=sym)
                    else:
                        with get_sync_session() as session:
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
                        inserted += 1
                        logger.debug("backfill_sym_ok", sym=sym, rows=len(rows))

                except Exception as e:
                    logger.warning("backfill_symbol_error", sym=sym, error=str(e))
                    errors += 1

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
