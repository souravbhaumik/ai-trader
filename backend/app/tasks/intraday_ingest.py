"""Intraday OHLCV ingestion task — runs every 15 min during market hours.

Fetches 15-minute candles for all active symbols and upserts into
ohlcv_intraday (5-day rolling table with TimescaleDB retention policy).

Data source priority (hybrid):
  1. Angel One getCandleData  — primary (system credentials from .env)
  2. Upstox historical-candle — fallback per-symbol if Angel One fails
  3. Raise / skip             — no yfinance for live data

Angel One system credentials (from env ANGEL_*) are used here, not per-user
credentials, because this is a system-level background data feed shared by all
signal generation. No user context is needed.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from sqlalchemy import text

from app.core.database import get_sync_session
from app.tasks.celery_app import celery_app
from app.tasks.task_utils import (
    append_task_log, clear_task_logs, now_iso, write_task_status,
)

logger = logging.getLogger(__name__)
_TASK       = "intraday_ingest"
_INTERVAL   = "15m"         # candle interval
_BATCH_SIZE = 25            # symbols per batch
_DELAY_SECS = 1.0           # rate-limit delay between batches


def _is_market_open() -> bool:
    """True if current IST time is within NSE market hours Mon–Fri."""
    now_ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    if now_ist.weekday() > 4:
        return False
    t = now_ist.time()
    from datetime import time as _time
    return _time(9, 15) <= t <= _time(15, 30)


def _fetch_via_angel_one(symbol: str, from_dt: datetime, to_dt: datetime) -> List[dict]:
    """Fetch 15-min candles from Angel One (sync). Returns list of bar dicts."""
    try:
        from app.core.config import settings
        if not settings.angel_api_key:
            return []

        import pyotp
        from SmartApi import SmartConnect  # type: ignore

        smart = SmartConnect(api_key=settings.angel_api_key)
        totp  = pyotp.TOTP(settings.angel_totp_secret).now()
        data  = smart.generateSession(settings.angel_client_id, settings.angel_mpin, totp)
        if not data.get("status"):
            logger.error("angel_one_system_auth_failed")
            return []

        # Resolve instrument token
        from app.services.angel_symbol_master import get_token_sync
        tok = get_token_sync(symbol)
        if not tok:
            return []

        fmt = "%Y-%m-%d %H:%M"
        params = {
            "exchange":    tok["exchange"],
            "symboltoken": tok["token"],
            "interval":    "FIFTEEN_MINUTE",
            "fromdate":    from_dt.strftime(fmt),
            "todate":      to_dt.strftime(fmt),
        }
        result = smart.getCandleData(params)
        smart.terminateSession(settings.angel_client_id)

        if not result or not result.get("status"):
            return []

        bars = []
        for row in result.get("data", []):
            if len(row) < 6:
                continue
            bars.append({
                "symbol": symbol, "ts": str(row[0]),
                "open": float(row[1]), "high": float(row[2]),
                "low": float(row[3]), "close": float(row[4]),
                "volume": int(row[5]), "source": "angel_one",
            })
        return bars
    except Exception as exc:
        logger.warning("angel_one_intraday_failed", symbol=symbol, err=str(exc))
        return []


def _fetch_via_upstox(symbol: str, from_dt: datetime, to_dt: datetime) -> List[dict]:
    """Fetch 15-min candles from Upstox using the system-level app credentials.

    Uses the most recently valid access_token from broker_credentials for any
    configured Upstox user. This is a read-only market data call.
    """
    try:
        import httpx
        from app.core.config import settings
        if not settings.upstox_api_key:
            return []

        # Get the freshest access token from any Upstox user
        with get_sync_session() as session:
            row = session.execute(
                text("""
                    SELECT access_token
                    FROM   broker_credentials
                    WHERE  broker_name = 'upstox'
                      AND  is_configured = TRUE
                      AND  access_token IS NOT NULL
                      AND  (access_token_expires_at IS NULL OR access_token_expires_at > NOW())
                    ORDER  BY last_verified DESC NULLS LAST
                    LIMIT  1
                """)
            ).first()

        if not row or not row.access_token:
            return []

        from app.core.security import decrypt_field
        token = decrypt_field(row.access_token)

        clean_sym       = symbol.upper().replace(".NS", "").replace(".BO", "")
        instrument_key  = f"NSE_EQ|{clean_sym}"
        from_date       = from_dt.strftime("%Y-%m-%d")
        to_date         = to_dt.strftime("%Y-%m-%d")

        import asyncio

        async def _fetch():
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.get(
                    f"https://api.upstox.com/v2/historical-candle/{instrument_key}/15minute/{to_date}/{from_date}",
                    headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                )
                r.raise_for_status()
                return r.json().get("data", {}).get("candles", [])

        candles = asyncio.run(_fetch())
        bars = []
        for c in candles:
            if len(c) < 6:
                continue
            bars.append({
                "symbol": symbol, "ts": str(c[0]),
                "open": float(c[1]), "high": float(c[2]),
                "low": float(c[3]), "close": float(c[4]),
                "volume": int(c[5]), "source": "upstox",
            })
        return bars
    except Exception as exc:
        logger.warning("upstox_intraday_failed", symbol=symbol, err=str(exc))
        return []


@celery_app.task(name="app.tasks.intraday_ingest.ingest_intraday")
def ingest_intraday():
    """Fetch 15-min candles for all active symbols and upsert into ohlcv_intraday.

    Runs every 15 min during market hours (9:15–15:30 IST Mon–Fri).
    Skips silently outside market hours.
    """
    if not _is_market_open():
        logger.info("intraday_ingest.skipped_outside_market_hours")
        return {"status": "skipped", "reason": "outside_market_hours"}

    started = now_iso()
    clear_task_logs(_TASK)
    write_task_status(_TASK, "running", "Intraday ingest started.", started_at=started)

    now_utc = datetime.now(timezone.utc)
    # Fetch today's candles from 9:00 IST to now
    today_open_ist = now_utc.astimezone(timezone(timedelta(hours=5, minutes=30))).replace(
        hour=9, minute=0, second=0, microsecond=0
    )
    from_dt = today_open_ist.astimezone(timezone.utc).replace(tzinfo=None)
    to_dt   = now_utc.replace(tzinfo=None)

    with get_sync_session() as session:
        symbols: List[str] = [
            r[0] for r in session.execute(
                text("SELECT symbol FROM stock_universe WHERE is_active = TRUE ORDER BY market_cap DESC NULLS LAST")
            ).fetchall()
        ]

    total     = len(symbols)
    upserted  = 0
    ao_count  = 0
    up_count  = 0
    skip_count = 0

    append_task_log(_TASK, f"Fetching intraday candles for {total} symbols…")

    # Angel One: batch authenticate once, then fetch all symbols
    # (getCandleData is per-symbol, so we loop but reuse the session)
    ao_session = _init_angel_one_session()

    for idx in range(0, total, _BATCH_SIZE):
        batch = symbols[idx: idx + _BATCH_SIZE]
        batch_bars: List[dict] = []

        for sym in batch:
            bars = []

            # 1. Try Angel One
            if ao_session:
                bars = _fetch_symbol_angel_one(ao_session, sym, from_dt, to_dt)
                if bars:
                    ao_count += len(bars)

            # 2. Upstox fallback
            if not bars:
                bars = _fetch_via_upstox(sym, from_dt, to_dt)
                if bars:
                    up_count += len(bars)

            if not bars:
                skip_count += 1
            else:
                batch_bars.extend(bars)

        if batch_bars:
            with get_sync_session() as session:
                session.execute(
                    text("""
                        INSERT INTO ohlcv_intraday
                            (symbol, ts, interval, open, high, low, close, volume, source)
                        VALUES
                            (:symbol, :ts, :interval, :open, :high, :low, :close, :volume, :source)
                        ON CONFLICT (symbol, ts, interval) DO UPDATE SET
                            open   = EXCLUDED.open,
                            high   = EXCLUDED.high,
                            low    = EXCLUDED.low,
                            close  = EXCLUDED.close,
                            volume = EXCLUDED.volume,
                            source = EXCLUDED.source
                    """),
                    [{**b, "interval": _INTERVAL} for b in batch_bars],
                )
                session.commit()
            upserted += len(batch_bars)

        time.sleep(_DELAY_SECS)

    # Close Angel One session
    if ao_session:
        try:
            from app.core.config import settings
            ao_session.terminateSession(settings.angel_client_id)
        except Exception:
            pass

    msg = (
        f"Intraday ingest done: {upserted} bars upserted "
        f"(AO={ao_count} UP={up_count} skip={skip_count})"
    )
    append_task_log(_TASK, msg)
    write_task_status(
        _TASK, "done", msg,
        started_at=started, finished_at=now_iso(),
        summary={"upserted": upserted, "angel_one": ao_count, "upstox": up_count, "skipped": skip_count},
    )
    return {"status": "done", "upserted": upserted}


def _init_angel_one_session():
    """Authenticate a shared Angel One session for system-level data fetches."""
    try:
        from app.core.config import settings
        if not settings.angel_api_key or not settings.angel_totp_secret:
            return None

        import pyotp
        from SmartApi import SmartConnect  # type: ignore

        smart = SmartConnect(api_key=settings.angel_api_key)
        totp  = pyotp.TOTP(settings.angel_totp_secret).now()
        data  = smart.generateSession(settings.angel_client_id, settings.angel_mpin, totp)
        if data.get("status"):
            return smart
        logger.error("angel_one_system_session_failed")
        return None
    except Exception as exc:
        logger.error("angel_one_system_session_error: %s", exc)
        return None


def _fetch_symbol_angel_one(smart_api, symbol: str, from_dt: datetime, to_dt: datetime) -> List[dict]:
    """Fetch 15-min candles for one symbol using a pre-authenticated SmartAPI session."""
    try:
        from app.services.angel_symbol_master import get_token_sync
        tok = get_token_sync(symbol)
        if not tok:
            return []

        fmt = "%Y-%m-%d %H:%M"
        params = {
            "exchange":    tok["exchange"],
            "symboltoken": tok["token"],
            "interval":    "FIFTEEN_MINUTE",
            "fromdate":    from_dt.strftime(fmt),
            "todate":      to_dt.strftime(fmt),
        }
        result = smart_api.getCandleData(params)
        if not result or not result.get("status"):
            return []

        bars = []
        for row in result.get("data", []):
            if len(row) < 6:
                continue
            bars.append({
                "symbol": symbol, "ts": str(row[0]),
                "open": float(row[1]), "high": float(row[2]),
                "low": float(row[3]), "close": float(row[4]),
                "volume": int(row[5]), "source": "angel_one",
            })
        return bars
    except Exception as exc:
        logger.warning("angel_one_symbol_fetch_failed", symbol=symbol, err=str(exc))
        return []
