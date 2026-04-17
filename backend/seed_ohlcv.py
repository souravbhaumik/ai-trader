"""Generates synthetic realistic OHLCV data for top 100 symbols.

Used to TEST the Phase 3 ML pipeline (training, inference, signal blending)
while Yahoo Finance rate limits are in effect.

The data is a random walk with GBM-style simulation, seeded per symbol for
reproducibility. Replace with real backfill once rate limits clear.

Run inside the celery-worker container:
    PYTHONPATH=/app python /app/seed_ohlcv.py
"""
import hashlib
import random
import math
from datetime import datetime, timedelta

import structlog
from sqlalchemy import text
from app.core.database import get_sync_session

logger = structlog.get_logger(__name__)

NUM_SYMBOLS  = 100   # enough for LightGBM training
DAYS         = 365   # 1 year of data
TRADING_DAYS = 252   # approximate

def _sym_seed(sym: str) -> int:
    return int(hashlib.md5(sym.encode()).hexdigest()[:8], 16)


def gen_ohlcv(sym: str, days: int = DAYS) -> list[dict]:
    """Simulate GBM daily prices for a symbol."""
    rng   = random.Random(_sym_seed(sym))
    start = datetime(2025, 1, 1)

    # Base price between ₹100 and ₹5000
    price = rng.uniform(100, 5000)
    mu    = 0.0003         # daily drift
    sigma = 0.015          # daily volatility (~24% annual)

    rows = []
    day_offset = 0
    for i in range(days):
        # Skip weekends
        date = start + timedelta(days=day_offset)
        while date.weekday() >= 5:
            day_offset += 1
            date = start + timedelta(days=day_offset)
        day_offset += 1

        ret    = mu + sigma * rng.gauss(0, 1)
        close  = price * math.exp(ret)
        open_  = price * math.exp(rng.gauss(0, sigma * 0.3))
        high   = max(open_, close) * (1 + rng.uniform(0, sigma))
        low    = min(open_, close) * (1 - rng.uniform(0, sigma))
        volume = int(rng.uniform(100_000, 5_000_000))

        rows.append({
            "symbol": sym,
            "ts":     date.replace(hour=0, minute=0, second=0),
            "open":   round(open_, 2),
            "high":   round(high, 2),
            "low":    round(low, 2),
            "close":  round(close, 2),
            "volume": volume,
            "source": "synthetic",
        })
        price = close

    return rows


def seed():
    with get_sync_session() as session:
        symbols = [
            r[0] for r in session.execute(
                text(
                    "SELECT symbol FROM stock_universe "
                    "WHERE is_active = TRUE ORDER BY market_cap DESC NULLS LAST "
                    f"LIMIT {NUM_SYMBOLS}"
                )
            ).fetchall()
        ]

    print(f"Seeding {len(symbols)} symbols × {DAYS} days…")

    for i, sym in enumerate(symbols, 1):
        rows = gen_ohlcv(sym)
        with get_sync_session() as session:
            session.execute(
                text("""
                    INSERT INTO ohlcv_daily
                        (symbol, ts, open, high, low, close, volume, source)
                    VALUES
                        (:symbol, :ts, :open, :high, :low, :close, :volume, :source)
                    ON CONFLICT (symbol, ts) DO NOTHING
                """),
                rows,
            )
            session.commit()
        print(f"  [{i}/{len(symbols)}] {sym}: {len(rows)} rows")

    with get_sync_session() as session:
        cnt = session.execute(text("SELECT COUNT(*) FROM ohlcv_daily")).scalar()
    print(f"\nDone. Total rows in ohlcv_daily: {cnt}")


if __name__ == "__main__":
    seed()
