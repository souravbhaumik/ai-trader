"""Targeted backfill: downloads top 200 symbols individually with delays.

Avoids yfinance rate limits by downloading one symbol at a time.
Run inside the celery-worker container:
    PYTHONPATH=/app python /app/run_backfill_top.py
"""
import time
import yfinance as yf
import pandas as pd
import structlog
from sqlalchemy import text
from app.core.database import get_sync_session

logger = structlog.get_logger(__name__)

TOP_N        = 200   # symbols to backfill
PERIOD       = "1y"
DELAY        = 5.0   # seconds between requests
RATE_LIMIT_PAUSE = 90.0  # seconds to wait after a rate-limit hit
MAX_RETRIES  = 3     # retries per symbol

def run():
    with get_sync_session() as session:
        rows_q = session.execute(
            text(
                "SELECT symbol FROM stock_universe "
                "WHERE is_active = TRUE ORDER BY market_cap DESC NULLS LAST "
                f"LIMIT {TOP_N}"
            )
        ).fetchall()
        symbols = [r[0] for r in rows_q]

    print(f"Backfilling {len(symbols)} symbols…")
    inserted = 0
    errors   = 0

    for i, sym in enumerate(symbols, 1):
        ticker = f"{sym}.NS"
        success = False
        for attempt in range(MAX_RETRIES):
            try:
                df = yf.Ticker(ticker).history(period=PERIOD, auto_adjust=True)
                if df is None or df.empty:
                    print(f"  [{i}/{len(symbols)}] {sym}: no data")
                    break

                rows = []
                for ts, row in df.iterrows():
                    rows.append({
                        "symbol": sym,
                        "ts":     ts.to_pydatetime().replace(tzinfo=None),
                        "open":   float(row["Open"]),
                        "high":   float(row["High"]),
                        "low":    float(row["Low"]),
                        "close":  float(row["Close"]),
                        "volume": int(row["Volume"] or 0),
                        "source": "yfinance",
                    })

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
                print(f"  [{i}/{len(symbols)}] {sym}: {len(rows)} rows OK")
                success = True
                break

            except Exception as e:
                err = str(e)
                if "RateLimit" in err or "Too Many Requests" in err:
                    if attempt < MAX_RETRIES - 1:
                        print(f"  [{i}/{len(symbols)}] {sym}: rate-limited, waiting {RATE_LIMIT_PAUSE}s (attempt {attempt+1}/{MAX_RETRIES})")
                        time.sleep(RATE_LIMIT_PAUSE)
                    else:
                        print(f"  [{i}/{len(symbols)}] {sym}: ERROR (rate-limit exhausted) – {e}")
                        errors += 1
                        break
                else:
                    print(f"  [{i}/{len(symbols)}] {sym}: ERROR – {e}")
                    errors += 1
                    break

        time.sleep(DELAY)

    print(f"\nDone. {inserted}/{len(symbols)} symbols inserted, {errors} errors.")

if __name__ == "__main__":
    run()
