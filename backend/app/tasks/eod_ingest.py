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

logger = structlog.get_logger(__name__)

_BATCH_SIZE = 20
_DELAY_SECS = 2.0


@celery_app.task(name="app.tasks.eod_ingest.ingest_eod")
def ingest_eod():
    """Fetch the latest OHLCV day for all active symbols and upsert into ohlcv_daily."""
    import pandas as pd
    import yfinance as yf

    with get_sync_session() as session:
        symbols = [
            r[0] for r in session.execute(
                text(
                    "SELECT symbol FROM stock_universe"
                    " WHERE is_active = TRUE ORDER BY market_cap DESC NULLS LAST"
                )
            ).fetchall()
        ]

        total_inserted = 0

        for i in range(0, len(symbols), _BATCH_SIZE):
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

            time.sleep(_DELAY_SECS)

        logger.info("eod_ingest_done", inserted=total_inserted)
        return {"status": "done", "inserted": total_inserted}


def _run_eod(session):
    from sqlalchemy import text
    import yfinance as yf

    symbols = [
        r[0] for r in session.execute(
            text("SELECT symbol FROM stock_universe WHERE is_active = TRUE ORDER BY market_cap DESC NULLS LAST")
        ).fetchall()
    ]

        import time
        batch_size = 20
        total_inserted = 0

        for i in range(0, len(symbols), batch_size):
            batch = symbols[i: i + batch_size]
            try:
                data = yf.download(
                    batch, period="5d", interval="1d",
                    progress=False, auto_adjust=True, threads=True,
                )
                if data is None or data.empty:
                    continue

                import pandas as pd

                # ── Normalise yfinance output ─────────────────────────────────
                # When multiple tickers are requested but only **one** succeeds,
                # yfinance silently drops the MultiIndex and returns a flat
                # DataFrame. Detect this and either re-wrap (single-sym batch)
                # or skip the batch (ambiguous multi-sym result).
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
                            reason="yfinance returned non-MultiIndex for multi-sym batch",
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
                        c = close_df[sym]; o = open_df[sym]; h = high_df[sym]
                        lo = low_df[sym]; v = vol_df[sym]

                        latest_ts = c.dropna().index[-1]
                        rows.append((
                            sym,
                            latest_ts.to_pydatetime().replace(tzinfo=None),
                            float(o.get(latest_ts, c.iloc[-1])),
                            float(h.get(latest_ts, c.iloc[-1])),
                            float(lo.get(latest_ts, c.iloc[-1])),
                            float(c.iloc[-1]),
                            int(v.iloc[-1] or 0),
                            "yfinance",
                        ))
                    except Exception:
                        pass

                if rows:
                    session.execute(
                        text("""
                            INSERT INTO ohlcv_daily (symbol, ts, open, high, low, close, volume, source)
                            VALUES (:symbol, :ts, :open, :high, :low, :close, :volume, :source)
                            ON CONFLICT (symbol, ts) DO UPDATE SET
                                close = EXCLUDED.close, volume = EXCLUDED.volume
                        """),
                        [
                            {"symbol": r[0], "ts": r[1], "open": r[2], "high": r[3],
                             "low": r[4], "close": r[5], "volume": r[6], "source": r[7]}
                            for r in rows
                        ],
                    )
                    session.commit()
                    total_inserted += len(rows)

            except Exception as e:
                logger.error("eod_batch_error", error=str(e))

            time.sleep(2)

        logger.info("eod_ingest_done", inserted=total_inserted)
        return {"status": "done", "inserted": total_inserted}
