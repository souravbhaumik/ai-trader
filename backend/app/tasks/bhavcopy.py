"""NSE Bhavcopy daily OHLCV ingestion task.

Downloads the official NSE Equity Bhavcopy ZIP from the NSE archives,
extracts OHLCV data for EQ-series symbols, and upserts into ohlcv_daily.

Bhavcopy URL format (NSE archives — no auth required):
  https://archives.nseindia.com/content/historical/EQUITIES/{YYYY}/{MON}/cm{DD}{MON}{YYYY}bhav.csv.zip

Stale-date retry:
  NSE sometimes publishes the previous day's file for a few hours after market
  close on high-volume days.  The task validates the TIMESTAMP column inside
  the CSV against the expected trade date.  If they don't match the task waits
  15 minutes and retries, up to _MAX_RETRIES times.

Celery Beat schedule:  Mon–Fri at 19:30 IST (NSE publishes by ~18:30 IST).
Admin can also trigger manually from the admin panel.
"""
from __future__ import annotations

import time
from datetime import date, datetime

import structlog
from sqlalchemy import text

from app.core.database import get_sync_session
from app.tasks.celery_app import celery_app
from app.tasks.nse_utils import bhavcopy_archive_url, download_bhavcopy_zip
from app.tasks.task_utils import clear_task_logs, now_iso, write_task_status

logger = structlog.get_logger(__name__)

_MAX_RETRIES       = 4
_RETRY_WAIT_SECS   = 15 * 60   # 15 minutes

_TASK_NAME = "bhavcopy"


@celery_app.task(name="app.tasks.bhavcopy.ingest_bhavcopy")
def ingest_bhavcopy(trade_date_str: str | None = None) -> dict:
    """Download NSE Bhavcopy and upsert into ohlcv_daily.

    Args:
        trade_date_str: ISO date string (e.g. "2026-04-17").  Defaults to today.
    """
    trade_date = date.fromisoformat(trade_date_str) if trade_date_str else date.today()
    started    = now_iso()
    clear_task_logs(_TASK_NAME)

    write_task_status(
        _TASK_NAME, "running",
        f"Downloading Bhavcopy for {trade_date}…",
        started_at=started,
    )
    logger.info("bhavcopy.start", trade_date=str(trade_date))

    # ── Download with stale-date retry ───────────────────────────────────────
    # A 404 means NSE didn't publish a file — almost always a market holiday.
    # Distinguish that clean skip from a genuine network/stale-date failure.
    import requests as _req

    holiday_skip = False
    df = None
    for attempt in range(1, _MAX_RETRIES + 1):
        url = bhavcopy_archive_url(trade_date)
        try:
            _probe = _req.head(url, timeout=10)
            if _probe.status_code == 404:
                holiday_skip = True
                break          # no point retrying — NSE won't publish on a holiday
        except Exception:
            pass               # fall through to download which logs the error

        df = download_bhavcopy_zip(trade_date)
        if df is not None:
            break
        if attempt < _MAX_RETRIES:
            retry_msg = (
                f"NSE file not ready or stale — "
                f"retry {attempt}/{_MAX_RETRIES - 1} in 15 min"
            )
            write_task_status(_TASK_NAME, "running", retry_msg, started_at=started)
            logger.info("bhavcopy.waiting", attempt=attempt)
            time.sleep(_RETRY_WAIT_SECS)

    if holiday_skip:
        msg = f"No Bhavcopy for {trade_date} — likely a market holiday or weekend."
        write_task_status(_TASK_NAME, "done", msg, started_at=started, finished_at=now_iso())
        logger.info("bhavcopy.holiday_skip", trade_date=str(trade_date))
        return {"status": "skipped", "date": str(trade_date), "message": msg}

    if df is None:
        msg = f"Bhavcopy for {trade_date} unavailable after {_MAX_RETRIES} attempts."
        write_task_status(_TASK_NAME, "error", msg, started_at=started, finished_at=now_iso())
        logger.error("bhavcopy.failed", trade_date=str(trade_date))
        return {"status": "error", "date": str(trade_date), "message": msg}

    # ── Filter EQ (regular equity) series only ───────────────────────────────
    df = df[df["SERIES"].str.strip() == "EQ"].copy()
    df.rename(columns={
        "SYMBOL":   "symbol",
        "OPEN":     "open",
        "HIGH":     "high",
        "LOW":      "low",
        "CLOSE":    "close",
        "TOTTRDQTY": "volume",
    }, inplace=True)

    # ── Upsert into ohlcv_daily ───────────────────────────────────────────────
    inserted = 0
    errors   = 0

    with get_sync_session() as session:
        for _, row in df.iterrows():
            try:
                session.execute(
                    text("""
                        INSERT INTO ohlcv_daily
                            (symbol, ts, open, high, low, close, volume, source)
                        VALUES
                            (:sym, :dt, :o, :h, :l, :c, :v, 'bhavcopy')
                        ON CONFLICT (symbol, ts) DO UPDATE SET
                            open   = EXCLUDED.open,
                            high   = EXCLUDED.high,
                            low    = EXCLUDED.low,
                            close  = EXCLUDED.close,
                            volume = EXCLUDED.volume,
                            source = EXCLUDED.source
                    """),
                    {
                        "sym": str(row["symbol"]).strip(),
                        "dt":  trade_date,
                        "o":   float(row["open"]),
                        "h":   float(row["high"]),
                        "l":   float(row["low"]),
                        "c":   float(row["close"]),
                        "v":   int(row["volume"]),
                    },
                )
                inserted += 1
            except Exception as exc:
                errors += 1
                logger.debug("bhavcopy.row_error", symbol=row.get("symbol"), err=str(exc))

        session.commit()

    summary = {"date": str(trade_date), "inserted": inserted, "errors": errors}
    msg     = f"Bhavcopy {trade_date}: {inserted} rows upserted, {errors} errors."
    write_task_status(
        _TASK_NAME, "done", msg,
        started_at=started, finished_at=now_iso(),
        summary=summary,
    )
    logger.info("bhavcopy.done", **summary)
    return {"status": "done", **summary}
