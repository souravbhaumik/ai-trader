"""Broker API historical OHLCV backfill — Angel One SmartAPI implementation.

Credentials required in .env:
  BROKER_NAME=angel_one
  ANGEL_API_KEY=...
  ANGEL_CLIENT_ID=...        # Angel One login ID
  ANGEL_MPIN=...             # 4-digit trading PIN
  ANGEL_TOTP_SECRET=...      # base32 TOTP secret (shown once during TOTP setup)

Angel One rate limits:
  Historical candle API: ~1 req/s (enforced via 1.1s sleep between symbols)
  Max date range per call: 400 days for ONE_DAY interval

Symbol master (symbol → token mapping):
  https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json
"""
from __future__ import annotations

import time
from datetime import date, timedelta
from urllib.parse import urlparse

import psycopg2
import requests
import structlog
from psycopg2.extras import execute_values

from app.tasks.celery_app import celery_app
from app.tasks.task_utils import now_iso, write_task_status

logger = structlog.get_logger(__name__)

_TASK_NAME = "broker_backfill"
_SCRIP_MASTER_URL = (
    "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
)
_PERIOD_DAYS: dict[str, int] = {"1y": 365, "2y": 730, "5y": 1825}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _login(settings):
    """Authenticate with Angel One SmartAPI; returns SmartConnect or None."""
    try:
        import pyotp
        from SmartApi import SmartConnect  # type: ignore

        smart = SmartConnect(api_key=settings.angel_api_key)
        totp = pyotp.TOTP(settings.angel_totp_secret).now()
        resp = smart.generateSession(settings.angel_client_id, settings.angel_mpin, totp)
        if resp.get("status"):
            logger.info("angel_one.login_ok", client_id=settings.angel_client_id)
            return smart
        logger.error("angel_one.login_failed", response=resp)
        return None
    except Exception as exc:
        logger.error("angel_one.login_error", error=str(exc))
        return None


def _load_symbol_token_map() -> dict[str, str]:
    """Download Angel One scrip master; return {NSE_SYMBOL: token} for EQ series."""
    try:
        resp = requests.get(_SCRIP_MASTER_URL, timeout=30)
        resp.raise_for_status()
        master = resp.json()
        token_map: dict[str, str] = {}
        for item in master:
            if item.get("exch_seg") == "NSE" and item.get("symbol", "").endswith("-EQ"):
                base_sym = item["symbol"].replace("-EQ", "")
                token_map[base_sym] = item["token"]
        logger.info("angel_one.scrip_master_loaded", symbols=len(token_map))
        return token_map
    except Exception as exc:
        logger.error("angel_one.scrip_master_error", error=str(exc))
        return {}


def _fetch_candles(smart, token: str, from_date: date, to_date: date) -> list[dict]:
    """Fetch ONE_DAY candles for a token; splits into ≤400-day chunks."""
    results: list[dict] = []
    cursor = from_date
    while cursor <= to_date:
        chunk_end = min(cursor + timedelta(days=399), to_date)
        try:
            resp = smart.getCandleData({
                "exchange":    "NSE",
                "symboltoken": token,
                "interval":    "ONE_DAY",
                "fromdate":    cursor.strftime("%Y-%m-%d 09:00"),
                "todate":      chunk_end.strftime("%Y-%m-%d 15:30"),
            })
            if resp.get("status") and resp.get("data"):
                for row in resp["data"]:
                    ts, o, h, l, c, v = row
                    results.append({
                        "trade_date": ts[:10],
                        "open": float(o), "high": float(h),
                        "low":  float(l), "close": float(c),
                        "volume": int(v),
                    })
        except Exception as exc:
            logger.warning("angel_one.candle_error", token=token, error=str(exc))
        cursor = chunk_end + timedelta(days=1)
        time.sleep(1.1)  # 1 req/s rate limit
    return results


def _upsert_ohlcv(conn, symbol: str, rows: list[dict]) -> int:
    """Bulk-upsert OHLCV rows for one symbol; returns count upserted."""
    if not rows:
        return 0
    records = [
        (symbol, r["trade_date"], r["open"], r["high"], r["low"], r["close"], r["volume"], "angel_one")
        for r in rows
    ]
    with conn.cursor() as cur:
        execute_values(
            cur,
            """
            INSERT INTO ohlcv_daily (symbol, trade_date, open, high, low, close, volume, source)
            VALUES %s
            ON CONFLICT (symbol, trade_date) DO UPDATE SET
                open   = EXCLUDED.open,
                high   = EXCLUDED.high,
                low    = EXCLUDED.low,
                close  = EXCLUDED.close,
                volume = EXCLUDED.volume,
                source = EXCLUDED.source
            """,
            records,
        )
    conn.commit()
    return len(records)


# ── Celery task ───────────────────────────────────────────────────────────────

@celery_app.task(name="app.tasks.broker_backfill.run_broker_backfill",
                 bind=True, max_retries=1, default_retry_delay=60)
def run_broker_backfill(self, period: str = "1y") -> dict:
    """Historical OHLCV backfill via Angel One SmartAPI.

    Fetches ONE_DAY candles for all active symbols in stock_universe and
    upserts into ohlcv_daily. Run once after initial setup; use NSE Bhavcopy
    for daily updates thereafter.
    """
    started = now_iso()
    from app.core.config import settings

    # ── 1. Validate credentials ───────────────────────────────────────────────
    broker = settings.broker_name.strip().lower()
    if broker != "angel_one":
        msg = (
            f"BROKER_NAME is '{broker or '(not set)'}'. "
            "Set BROKER_NAME=angel_one in .env and restart containers."
        )
        write_task_status(_TASK_NAME, "error", msg, started_at=started, finished_at=now_iso())
        return {"status": "not_configured", "message": msg}

    missing = [
        k.upper() for k in ("angel_api_key", "angel_client_id", "angel_mpin", "angel_totp_secret")
        if not getattr(settings, k, "")
    ]
    if missing:
        msg = f"Missing credentials: {', '.join(missing)}. Add to .env and restart."
        write_task_status(_TASK_NAME, "error", msg, started_at=started, finished_at=now_iso())
        return {"status": "missing_credentials", "message": msg}

    write_task_status(_TASK_NAME, "running", "Logging in to Angel One…", started_at=started)

    # ── 2. Login ──────────────────────────────────────────────────────────────
    smart = _login(settings)
    if not smart:
        msg = "Angel One login failed. Check ANGEL_CLIENT_ID / ANGEL_MPIN / ANGEL_TOTP_SECRET."
        write_task_status(_TASK_NAME, "error", msg, started_at=started, finished_at=now_iso())
        return {"status": "login_failed", "message": msg}

    # ── 3. Load scrip master ──────────────────────────────────────────────────
    write_task_status(_TASK_NAME, "running", "Loading scrip master…", started_at=started)
    token_map = _load_symbol_token_map()
    if not token_map:
        msg = "Failed to download Angel One scrip master (symbol→token map)."
        write_task_status(_TASK_NAME, "error", msg, started_at=started, finished_at=now_iso())
        return {"status": "scrip_master_failed", "message": msg}

    # ── 4. Load symbols from stock_universe ───────────────────────────────────
    db_url = settings.sync_database_url.replace("postgresql+psycopg2://", "postgresql://")
    parsed = urlparse(db_url)
    conn = psycopg2.connect(
        host=parsed.hostname, port=parsed.port or 5432,
        dbname=parsed.path.lstrip("/"),
        user=parsed.username, password=parsed.password,
    )

    with conn.cursor() as cur:
        cur.execute("SELECT symbol FROM stock_universe WHERE is_active = TRUE ORDER BY symbol")
        symbols = [row[0] for row in cur.fetchall()]

    if not symbols:
        msg = "stock_universe is empty. Run 'Populate Universe' from admin panel first."
        write_task_status(_TASK_NAME, "error", msg, started_at=started, finished_at=now_iso())
        conn.close()
        return {"status": "no_symbols", "message": msg}

    # ── 5. Backfill each symbol ───────────────────────────────────────────────
    days = _PERIOD_DAYS.get(period, 365)
    to_date = date.today()
    from_date = to_date - timedelta(days=days)
    total = len(symbols)
    done = skipped = rows_inserted = 0

    write_task_status(
        _TASK_NAME, "running",
        f"Backfilling {total} symbols ({period}) via Angel One…",
        started_at=started,
        summary={"total": total, "done": 0, "skipped": 0, "rows": 0},
    )

    for symbol in symbols:
        token = token_map.get(symbol)
        if not token:
            skipped += 1
            continue

        rows = _fetch_candles(smart, token, from_date, to_date)
        rows_inserted += _upsert_ohlcv(conn, symbol, rows)
        done += 1

        if done % 10 == 0:
            write_task_status(
                _TASK_NAME, "running",
                f"Progress: {done}/{total} symbols…",
                started_at=started,
                summary={"total": total, "done": done, "skipped": skipped, "rows": rows_inserted},
            )
            logger.info("angel_one.progress", done=done, total=total, rows=rows_inserted)

    conn.close()

    # ── 6. Logout ─────────────────────────────────────────────────────────────
    try:
        smart.terminateSession(settings.angel_client_id)
    except Exception:
        pass

    summary = {"total": total, "done": done, "skipped": skipped, "rows_inserted": rows_inserted}
    msg = f"Done: {done}/{total} symbols, {rows_inserted} rows upserted ({skipped} skipped — no token)."
    write_task_status(_TASK_NAME, "done", msg, started_at=started, finished_at=now_iso(), summary=summary)
    logger.info("angel_one.backfill_complete", **summary)
    return {"status": "done", **summary}
