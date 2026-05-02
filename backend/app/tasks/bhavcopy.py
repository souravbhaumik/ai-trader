"""NSE Bhavcopy daily OHLCV ingestion task — Phase 11 upgrade.

Downloads the NSE Security-wise Bhavdata CSV which includes Delivery Percentage
(DELIV_PER), replacing the old ZIP archive which lacked delivery data.

Source URL: nsearchives.nseindia.com/products/content/sec_bhavdata_full_{DDMMYYYY}.csv

Stale-date retry:
  NSE sometimes publishes the previous day's file for a few hours after market
  close on high-volume days. The task retries up to _MAX_RETRIES times with
  15-minute waits before giving up.

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
from app.tasks.nse_utils import download_sec_bhav_csv
from app.tasks.task_utils import clear_task_logs, now_iso, write_task_status

logger = structlog.get_logger(__name__)

_MAX_RETRIES     = 4
_RETRY_WAIT_SECS = 15 * 60   # 15 minutes
_TASK_NAME       = "bhavcopy"


@celery_app.task(name="app.tasks.bhavcopy.ingest_bhavcopy")
def ingest_bhavcopy(trade_date_str: str | None = None) -> dict:
    """Download NSE Security-wise Bhavdata and upsert into ohlcv_daily.

    Phase 11: switched from the old ZIP bhavcopy (no delivery data) to the
    security-wise bhavdata CSV which includes DELIV_PER.

    Args:
        trade_date_str: ISO date string (e.g. "2026-04-17").  Defaults to today.
    """
    trade_date = date.fromisoformat(trade_date_str) if trade_date_str else date.today()
    started    = now_iso()
    clear_task_logs(_TASK_NAME)

    write_task_status(
        _TASK_NAME, "running",
        f"Downloading Security-wise Bhavdata for {trade_date}…",
        started_at=started,
    )
    logger.info("bhavcopy.start", trade_date=str(trade_date))

    # ── Download with stale-date retry ───────────────────────────────────────
    # download_sec_bhav_csv returns None when the file isn't available yet
    # (market holiday or NSE hasn't published it). We retry up to 4 times
    # with 15-minute intervals before marking as skipped.
    trade_dt = datetime.combine(trade_date, datetime.min.time())
    data = None

    for attempt in range(1, _MAX_RETRIES + 1):
        data = download_sec_bhav_csv(trade_dt)
        if data:
            break
        if attempt < _MAX_RETRIES:
            retry_msg = (
                f"NSE bhavdata not ready — "
                f"retry {attempt}/{_MAX_RETRIES - 1} in 15 min"
            )
            write_task_status(_TASK_NAME, "running", retry_msg, started_at=started)
            logger.info("bhavcopy.waiting", attempt=attempt)
            time.sleep(_RETRY_WAIT_SECS)

    if data is None:
        msg = (
            f"Bhavdata for {trade_date} unavailable after {_MAX_RETRIES} attempts "
            "(market holiday or NSE delay)."
        )
        write_task_status(_TASK_NAME, "done", msg, started_at=started, finished_at=now_iso())
        logger.info("bhavcopy.skipped", trade_date=str(trade_date))
        return {"status": "skipped", "date": str(trade_date), "message": msg}

    # ── Upsert into ohlcv_daily ───────────────────────────────────────────────
    # IMPORTANT: delivery_pct is already normalised to [0.0, 1.0] by nse_utils.
    # DO NOT divide by 100 again here — that bug would store 0.0045 instead of 0.45.
    inserted = 0
    errors   = 0

    with get_sync_session() as session:
        for sym, row in data.items():
            try:
                session.execute(
                    text("""
                        INSERT INTO ohlcv_daily
                            (symbol, ts, open, high, low, close, volume, delivery_pct, source)
                        VALUES
                            (:sym, :dt, :o, :h, :l, :c, :v, :dp, 'bhavcopy')
                        ON CONFLICT (symbol, ts) DO UPDATE SET
                            open         = EXCLUDED.open,
                            high         = EXCLUDED.high,
                            low          = EXCLUDED.low,
                            close        = EXCLUDED.close,
                            volume       = EXCLUDED.volume,
                            delivery_pct = EXCLUDED.delivery_pct,
                            source       = EXCLUDED.source
                    """),
                    {
                        "sym": sym,
                        "dt":  trade_date,
                        "o":   row["open"],
                        "h":   row["high"],
                        "l":   row["low"],
                        "c":   row["close"],
                        "v":   row["volume"],
                        "dp":  row.get("delivery_pct"),   # None for non-equity / dash entries
                    },
                )
                inserted += 1
            except Exception as exc:
                errors += 1
                logger.debug("bhavcopy.row_error", symbol=sym, err=str(exc))

        session.commit()

    summary = {"date": str(trade_date), "inserted": inserted, "errors": errors}
    msg     = f"Bhavcopy {trade_date}: {inserted} rows upserted ({errors} errors)."
    write_task_status(
        _TASK_NAME, "done", msg,
        started_at=started, finished_at=now_iso(),
        summary=summary,
    )
    logger.info("bhavcopy.done", **summary)
    return {"status": "done", **summary}
