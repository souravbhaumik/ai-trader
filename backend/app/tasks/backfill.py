"""Historical OHLCV backfill via NSE Bhavcopy.

Iterates over calendar dates from (today - days) to yesterday, fetches the
official NSE Bhavcopy CSV for each trading day, and bulk-upserts all EQ-series
rows into ohlcv_daily.  Weekends and exchange holidays return HTTP 404 from NSE
and are silently skipped — no manual holiday calendar needed.

Advantages over the old yfinance-per-symbol approach:
  - One HTTP request covers ALL ~500+ symbols for that day (not 500 requests)
  - Official NSE prices — no dividend/split adjustments, exact match with daily
    EOD ingest data
  - No rate-limit risk

Triggered manually from the Admin UI or via CLI:
    celery -A app.tasks.celery_app call app.tasks.backfill.backfill_universe

Progress is stored in Redis (key "backfill:progress") so the Admin UI can poll.
"""
from __future__ import annotations

import io
import json
import time
from datetime import datetime, timedelta

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

_PROGRESS_KEY = "backfill:progress"
_TASK_NAME    = "backfill"

_PERIOD_DAYS: dict[str, int] = {"1y": 365, "2y": 730, "5y": 1825}

_NSE_BHAVCOPY_URL = (
    "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{date}.csv"
)
_NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}

# Seconds to sleep between successful day-fetches to be polite to NSE
_DELAY_SECS = 1.0


def _get_redis():
    import redis
    from app.core.config import settings
    return redis.from_url(settings.redis_url, decode_responses=True)


def _set_progress(r, pct: int, message: str, status: str = "running") -> None:
    r.setex(
        _PROGRESS_KEY,
        3600,
        json.dumps({
            "pct": pct,
            "message": message,
            "status": status,
            "ts": datetime.utcnow().isoformat(),
        }),
    )
    # Mirror to task_utils so the View Logs modal shows live output
    level = "error" if status == "error" else "info"
    append_task_log(_TASK_NAME, f"[{pct:3d}%] {message}", level=level)


def _fetch_bhavcopy_day(client, trade_date: datetime) -> list[dict]:
    """Fetch one Bhavcopy CSV and return a list of OHLCV row dicts.

    Returns [] on 404 (weekend / holiday / future date) or parse error.
    """
    import pandas as pd

    date_str = trade_date.strftime("%d%m%Y")
    url = _NSE_BHAVCOPY_URL.format(date=date_str)

    try:
        resp = client.get(url, headers=_NSE_HEADERS)
    except Exception as exc:
        logger.warning("backfill.fetch_error", date=date_str, err=str(exc))
        return []

    if resp.status_code == 404:
        return []  # weekend or holiday — normal, not an error
    if resp.status_code != 200:
        logger.warning("backfill.unexpected_status", date=date_str, status=resp.status_code)
        return []

    try:
        df = pd.read_csv(io.StringIO(resp.text))
        df.columns = df.columns.str.strip()
        df = df[df["SERIES"].str.strip() == "EQ"].copy()
        if df.empty:
            return []

        # Parse the actual trade date from the CSV (DATE1 col)
        try:
            csv_date = datetime.strptime(
                df["DATE1"].iloc[0].strip(), "%d-%b-%Y"
            ).replace(hour=0, minute=0, second=0, microsecond=0)
        except Exception:
            csv_date = trade_date.replace(hour=0, minute=0, second=0, microsecond=0)

        rows = []
        for _, row in df.iterrows():
            symbol = str(row["SYMBOL"]).strip()
            try:
                rows.append({
                    "symbol": symbol,
                    "ts":     csv_date,
                    "open":   float(row["OPEN_PRICE"]),
                    "high":   float(row["HIGH_PRICE"]),
                    "low":    float(row["LOW_PRICE"]),
                    "close":  float(row["CLOSE_PRICE"]),
                    "volume": int(float(row["TTL_TRD_QNTY"])),
                    "source": "nse",
                })
            except Exception:
                pass
        return rows

    except Exception as exc:
        logger.warning("backfill.parse_error", date=date_str, err=str(exc))
        return []


@celery_app.task(bind=True, name="app.tasks.backfill.backfill_universe")
def backfill_universe(self, period: str = "2y", force: bool = False):
    """Download historical daily OHLCV from NSE Bhavcopy for all trading days
    in the requested period and bulk-upsert into ohlcv_daily.

    Args:
        period: one of "1y", "2y", "5y"  (default: "2y")
        force:  reserved for future use (currently ignored; upsert is idempotent)
    """
    import httpx

    r = _get_redis()

    # Clear stale logs and mark task as running in the shared task-status table
    clear_task_logs(_TASK_NAME)
    write_task_status(_TASK_NAME, "running", f"NSE Bhavcopy backfill ({period}) started.",
                      started_at=now_iso())

    days = _PERIOD_DAYS.get(period, 730)

    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    # Start from `days` ago; don't request today (Bhavcopy only available after ~6 PM IST)
    start_date = today - timedelta(days=days)
    end_date   = today - timedelta(days=1)

    # Build full date list (calendar days — NSE will 404 on non-trading days)
    date_list = []
    cur = start_date
    while cur <= end_date:
        if cur.weekday() < 5:   # skip Saturdays and Sundays upfront
            date_list.append(cur)
        cur += timedelta(days=1)

    total_dates = len(date_list)
    _set_progress(r, 0, f"Starting NSE Bhavcopy backfill ({period}: ~{total_dates} weekdays)…")
    logger.info("backfill.start", period=period, days=days, weekdays=total_dates)

    days_fetched  = 0
    days_skipped  = 0   # holidays / 404
    rows_inserted = 0
    errors        = 0

    try:
        # Single httpx session — establishes NSE homepage cookie once
        with httpx.Client(follow_redirects=True, timeout=30) as client:
            client.get("https://www.nseindia.com/", headers=_NSE_HEADERS)

            for idx, trade_date in enumerate(date_list):
                pct = int((idx / total_dates) * 95)
                if idx % 20 == 0:
                    _set_progress(
                        r, pct,
                        f"Fetching {trade_date.strftime('%d-%b-%Y')} "
                        f"({idx + 1}/{total_dates})…",
                    )

                rows = _fetch_bhavcopy_day(client, trade_date)

                if not rows:
                    days_skipped += 1
                    logger.debug("backfill.day_skipped", date=trade_date.date().isoformat())
                    continue

                try:
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
                    days_fetched  += 1
                    rows_inserted += len(rows)
                    logger.debug(
                        "backfill.day_ok",
                        date=trade_date.date().isoformat(),
                        rows=len(rows),
                    )
                    append_task_log(
                        _TASK_NAME,
                        f"{trade_date.strftime('%d-%b-%Y')}: {len(rows)} rows upserted.",
                    )
                except Exception as exc:
                    logger.warning(
                        "backfill.insert_error",
                        date=trade_date.date().isoformat(),
                        error=str(exc),
                    )
                    errors += 1
                    append_task_log(
                        _TASK_NAME,
                        f"{trade_date.strftime('%d-%b-%Y')}: insert error — {exc}",
                        level="error",
                    )

                time.sleep(_DELAY_SECS)

    except Exception as exc:
        _set_progress(r, 0, f"Backfill failed: {exc}", "error")
        write_task_status(_TASK_NAME, "error", str(exc), finished_at=now_iso())
        logger.error("backfill.failed", err=str(exc))
        raise

    final_msg = (
        f"Backfill complete ({period}). "
        f"{days_fetched} trading days fetched, "
        f"{rows_inserted:,} rows upserted, "
        f"{days_skipped} holidays/weekends skipped, "
        f"{errors} errors."
    )
    _set_progress(r, 100, final_msg, "done")
    write_task_status(_TASK_NAME, "done", final_msg, finished_at=now_iso(),
                      summary={"days_fetched": days_fetched, "rows_inserted": rows_inserted,
                               "days_skipped": days_skipped, "errors": errors})
    logger.info(
        "backfill.done",
        period=period,
        days_fetched=days_fetched,
        rows_inserted=rows_inserted,
        days_skipped=days_skipped,
        errors=errors,
    )
    return {
        "status": "done",
        "days_fetched": days_fetched,
        "rows_inserted": rows_inserted,
        "days_skipped": days_skipped,
        "errors": errors,
    }
