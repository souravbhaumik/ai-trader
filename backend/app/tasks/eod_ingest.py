"""EOD data ingestion task — runs daily at 4:30 PM IST on market days.

Downloads the latest day's OHLCV for all active symbols via yfinance
and upserts into ohlcv_daily. Lighter than a full backfill.
"""
from __future__ import annotations

import structlog

from app.tasks.celery_app import celery_app

logger = structlog.get_logger(__name__)


@celery_app.task(name="app.tasks.eod_ingest.ingest_eod")
def ingest_eod():
    """Fetch yesterday's (or today's after market close) data for all symbols."""
    import psycopg2
    import yfinance as yf

    from app.core.config import settings

    conn = psycopg2.connect(
        host=settings.db_host, port=settings.db_port,
        dbname=settings.db_name, user=settings.db_user, password=settings.db_password,
    )
    cur = conn.cursor()

    try:
        cur.execute(
            "SELECT symbol FROM stock_universe WHERE is_active = TRUE ORDER BY market_cap DESC NULLS LAST"
        )
        symbols = [r[0] for r in cur.fetchall()]

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
                close_df = data.get("Close")
                open_df  = data.get("Open")
                high_df  = data.get("High")
                low_df   = data.get("Low")
                vol_df   = data.get("Volume")

                rows = []
                for sym in batch:
                    try:
                        if isinstance(close_df, pd.Series):
                            c = close_df; o = open_df; h = high_df; lo = low_df; v = vol_df
                        else:
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
                    cur.executemany("""
                        INSERT INTO ohlcv_daily (symbol, ts, open, high, low, close, volume, source)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (symbol, ts) DO UPDATE SET
                            close = EXCLUDED.close, volume = EXCLUDED.volume
                    """, rows)
                    conn.commit()
                    total_inserted += len(rows)

            except Exception as e:
                logger.error("eod_batch_error", error=str(e))

            time.sleep(2)

        logger.info("eod_ingest_done", inserted=total_inserted)
        return {"status": "done", "inserted": total_inserted}

    finally:
        cur.close()
        conn.close()
