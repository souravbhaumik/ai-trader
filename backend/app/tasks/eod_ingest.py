"""EOD data ingestion task — runs daily at 4:30 PM IST on market days.

Downloads the latest day's OHLCV for all active symbols.

Primary source: NSE Bhavcopy (official NSE end-of-day CSV, authoritative Indian
market data, no rate limits, symbols match our DB format exactly).
Fallback: yfinance (used if NSE Bhavcopy is unavailable).
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Dict, Optional

import structlog
from sqlalchemy import text

from app.core.database import get_sync_session
from app.tasks.celery_app import celery_app
from app.tasks.nse_utils import try_sec_bhav_with_lookback
from app.tasks.task_utils import (
    append_task_log, clear_task_logs, now_iso, write_task_status,
)

logger = structlog.get_logger(__name__)

_TASK = "eod_ingest"
_BATCH_SIZE = 20
_DELAY_SECS = 2.0


@celery_app.task(name="app.tasks.eod_ingest.ingest_eod")
def ingest_eod():
    """Fetch the latest OHLCV day for all active symbols and upsert into ohlcv_daily.

    Tries NSE Bhavcopy first; falls back to yfinance if unavailable.
    """
    started = now_iso()
    clear_task_logs(_TASK)
    write_task_status(_TASK, "running", "EOD ingest started.", started_at=started)

    with get_sync_session() as session:
        symbols = [
            r[0] for r in session.execute(
                text(
                    "SELECT symbol FROM stock_universe"
                    " WHERE is_active = TRUE ORDER BY market_cap DESC NULLS LAST"
                )
            ).fetchall()
        ]

        append_task_log(_TASK, f"Loaded {len(symbols)} active symbols from DB.")
        write_task_status(
            _TASK, "running", f"Fetching OHLCV for {len(symbols)} symbols…",
            started_at=started,
        )

        # ── 1. Try NSE Bhavcopy (primary) ─────────────────────────────────────
        append_task_log(_TASK, "Attempting NSE Bhavcopy download…")
        nse_data = try_sec_bhav_with_lookback()
        total_inserted = 0

        if nse_data:
            append_task_log(_TASK, f"NSE Bhavcopy fetched — {len(nse_data)} EQ rows.")
            rows = [nse_data[sym] for sym in symbols if sym in nse_data]

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
                total_inserted = len(rows)
                msg = f"NSE Bhavcopy: {total_inserted}/{len(symbols)} symbols ingested."
                append_task_log(_TASK, msg)
                write_task_status(
                    _TASK, "done", msg,
                    started_at=started, finished_at=now_iso(),
                    summary={"inserted": total_inserted, "source": "nse"},
                )
                return {"status": "done", "source": "nse", "inserted": total_inserted}

        # ── 2. Fallback: yfinance ─────────────────────────────────────────────
        append_task_log(_TASK, "NSE Bhavcopy unavailable — falling back to yfinance.")
        import pandas as pd
        import yfinance as yf

        n_batches = (len(symbols) + _BATCH_SIZE - 1) // _BATCH_SIZE

        for batch_idx, i in enumerate(range(0, len(symbols), _BATCH_SIZE)):
            batch = symbols[i: i + _BATCH_SIZE]
            # yfinance expects .NS suffix for NSE symbols
            yf_batch = [f"{s}.NS" for s in batch]
            sym_map = dict(zip(yf_batch, batch))  # yf_sym → plain sym
            try:
                data = yf.download(
                    yf_batch, period="5d", interval="1d",
                    progress=False, auto_adjust=True, threads=True,
                )
                if data is None or data.empty:
                    continue

                if not isinstance(data.columns, pd.MultiIndex):
                    if len(batch) == 1:
                        yf_sym = yf_batch[0]
                        data = pd.concat({yf_sym: data}, axis=1).swaplevel(axis=1)
                        data.columns = pd.MultiIndex.from_tuples(
                            [(pt, yf_sym) for pt in data.columns.get_level_values(0)]
                        )
                    else:
                        continue

                close_df = data["Close"]
                open_df  = data["Open"]
                high_df  = data["High"]
                low_df   = data["Low"]
                vol_df   = data["Volume"]

                rows = []
                for yf_sym, plain_sym in sym_map.items():
                    try:
                        if yf_sym not in close_df.columns:
                            continue
                        c = close_df[yf_sym]; o = open_df[yf_sym]
                        h = high_df[yf_sym]; lo = low_df[yf_sym]; v = vol_df[yf_sym]
                        latest_ts = c.dropna().index[-1]
                        rows.append({
                            "symbol": plain_sym,
                            "ts":     latest_ts.to_pydatetime().replace(tzinfo=None),
                            "open":   float(o.get(latest_ts, c.iloc[-1])),
                            "high":   float(h.get(latest_ts, c.iloc[-1])),
                            "low":    float(lo.get(latest_ts, c.iloc[-1])),
                            "close":  float(c.iloc[-1]),
                            "volume": int(v.iloc[-1] or 0),
                            "source": "yfinance",
                        })
                    except Exception:
                        pass

                if rows:
                    session.execute(
                        text("""
                            INSERT INTO ohlcv_daily
                                (symbol, ts, open, high, low, close, volume, source)
                            VALUES
                                (:symbol, :ts, :open, :high, :low, :close, :volume, :source)
                            ON CONFLICT (symbol, ts) DO UPDATE SET
                                close  = EXCLUDED.close,
                                volume = EXCLUDED.volume
                        """),
                        rows,
                    )
                    session.commit()
                    total_inserted += len(rows)
            except Exception as exc:
                logger.warning("eod_batch_failed", batch_preview=batch[:3], err=str(exc))

            if (batch_idx + 1) % 5 == 0 or batch_idx == n_batches - 1:
                write_task_status(
                    _TASK, "running",
                    f"Batch {batch_idx+1}/{n_batches} — {total_inserted} rows so far…",
                    started_at=started,
                )
            time.sleep(_DELAY_SECS)

        msg = f"yfinance fallback: {total_inserted} rows upserted across {len(symbols)} symbols."
        logger.info("eod_ingest_done", inserted=total_inserted, source="yfinance")
        write_task_status(
            _TASK, "done", msg,
            started_at=started, finished_at=now_iso(),
            summary={"inserted": total_inserted, "source": "yfinance"},
        )
        return {"status": "done", "source": "yfinance", "inserted": total_inserted}
