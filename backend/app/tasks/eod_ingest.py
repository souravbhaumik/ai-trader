"""EOD data ingestion task — runs daily at 4:30 PM IST on market days.

Downloads the latest day's OHLCV for all active symbols via yfinance
and upserts into ohlcv_daily. Uses the shared SQLAlchemy sync engine so
Celery workers participate in the same connection pool as the application.
"""
from __future__ import annotations

import time

import structlog
from sqlalchemy import text

from app.core.database import get_sync_session
from app.tasks.celery_app import celery_app
from app.tasks.task_utils import (
    append_task_log, clear_task_logs, now_iso, write_task_status,
)

logger = structlog.get_logger(__name__)

_TASK = "eod_ingest"
_BATCH_SIZE = 20
_DELAY_SECS = 2.0


@celery_app.task(name="app.tasks.eod_ingest.ingest_eod")
def ingest_eod():
    """Fetch the latest OHLCV day for all active symbols and upsert into ohlcv_daily."""
    import pandas as pd
    import yfinance as yf

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
        write_task_status(_TASK, "running", f"Fetching OHLCV for {len(symbols)} symbols…", started_at=started)

        total_inserted = 0
        n_batches = (len(symbols) + _BATCH_SIZE - 1) // _BATCH_SIZE

        for batch_idx, i in enumerate(range(0, len(symbols), _BATCH_SIZE)):
            batch = symbols[i: i + _BATCH_SIZE]
            try:
                data = yf.download(
                    batch, period="5d", interval="1d",
                    progress=False, auto_adjust=True, threads=True,
                )
                if data is None or data.empty:
                    continue

                # ── Normalise MultiIndex shape ─────────────────────────────────
                if not isinstance(data.columns, pd.MultiIndex):
                    if len(batch) == 1:
                        sym_name = batch[0]
                        data = pd.concat({sym_name: data}, axis=1).swaplevel(axis=1)
                        data.columns = pd.MultiIndex.from_tuples(
                            [(pt, sym_name) for pt in data.columns.get_level_values(0)]
                        )
                    else:
                        logger.warning(
                            "eod_batch_flat_result_skipped",
                            batch_preview=batch[:3],
                            reason="non-MultiIndex for multi-sym batch",
                        )
                        continue

                close_df = data["Close"]
                open_df  = data["Open"]
                high_df  = data["High"]
                low_df   = data["Low"]
                vol_df   = data["Volume"]

                rows = []
                for sym in batch:
                    try:
                        if sym not in close_df.columns:
                            continue
                        c = close_df[sym]; o = open_df[sym]
                        h = high_df[sym]; lo = low_df[sym]; v = vol_df[sym]
                        latest_ts = c.dropna().index[-1]
                        rows.append({
                            "symbol": sym,
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
                logger.error("eod_batch_error", error=str(exc))
                append_task_log(_TASK, f"Batch {batch_idx+1}/{n_batches} error: {exc}", level="error")

            if (batch_idx + 1) % 5 == 0 or batch_idx == n_batches - 1:
                write_task_status(
                    _TASK, "running",
                    f"Batch {batch_idx+1}/{n_batches} — {total_inserted} rows so far…",
                    started_at=started,
                )
            time.sleep(_DELAY_SECS)

        msg = f"EOD ingest done — {total_inserted} rows upserted across {len(symbols)} symbols."
        logger.info("eod_ingest_done", inserted=total_inserted)
        write_task_status(
            _TASK, "done", msg,
            started_at=started, finished_at=now_iso(),
            summary={"inserted": total_inserted, "symbols": len(symbols)},
        )
        return {"status": "done", "inserted": total_inserted}
