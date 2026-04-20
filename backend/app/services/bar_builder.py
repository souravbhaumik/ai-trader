"""1-minute OHLCV bar builder.

Aggregates real-time tick/quote data into 1-minute candle bars and stores
them in Redis (with optional DB persistence).

Usage:
    from app.services.bar_builder import BarBuilder

    builder = BarBuilder()
    builder.on_tick("RELIANCE", price=2450.5, volume=100, ts=datetime.now())
    bars = builder.flush_completed()  # returns list of completed 1-min bars
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

import structlog

from app.brokers.base import OHLCVBar

logger = structlog.get_logger(__name__)


@dataclass
class _PartialBar:
    """Accumulator for ticks within a single 1-minute window."""

    symbol: str
    minute_key: str  # "YYYY-MM-DDTHH:MM"
    open: float = 0.0
    high: float = float("-inf")
    low: float = float("inf")
    close: float = 0.0
    volume: int = 0
    tick_count: int = 0

    def update(self, price: float, volume: int) -> None:
        if self.tick_count == 0:
            self.open = price
        self.high = max(self.high, price)
        self.low = min(self.low, price)
        self.close = price
        self.volume += volume
        self.tick_count += 1

    def to_ohlcv(self) -> OHLCVBar:
        return OHLCVBar(
            symbol=self.symbol,
            timestamp=self.minute_key + ":00",
            open=self.open,
            high=self.high,
            low=self.low,
            close=self.close,
            volume=self.volume,
        )


def _minute_key(ts: datetime) -> str:
    """Truncate datetime to minute precision as ISO key."""
    return ts.strftime("%Y-%m-%dT%H:%M")


class BarBuilder:
    """Builds 1-minute OHLCV bars from real-time ticks.

    Thread-safe via asyncio.Lock. Maintains one partial bar per symbol.
    When a tick arrives for a new minute, the previous bar is finalized.
    """

    def __init__(self) -> None:
        self._bars: Dict[str, _PartialBar] = {}
        self._completed: List[OHLCVBar] = []
        self._lock = asyncio.Lock()

    async def on_tick(
        self,
        symbol: str,
        price: float,
        volume: int = 0,
        ts: Optional[datetime] = None,
    ) -> Optional[OHLCVBar]:
        """Process an incoming tick. Returns a completed bar if the minute rolled over."""
        if ts is None:
            ts = datetime.now(timezone.utc)
        mk = _minute_key(ts)

        async with self._lock:
            current = self._bars.get(symbol)

            if current is not None and current.minute_key != mk:
                # Minute rolled over — finalize the old bar
                completed = current.to_ohlcv()
                self._completed.append(completed)
                self._bars[symbol] = _PartialBar(symbol=symbol, minute_key=mk)
                self._bars[symbol].update(price, volume)
                return completed

            if current is None:
                self._bars[symbol] = _PartialBar(symbol=symbol, minute_key=mk)

            self._bars[symbol].update(price, volume)
            return None

    async def flush_completed(self) -> List[OHLCVBar]:
        """Return and clear all completed bars."""
        async with self._lock:
            bars = list(self._completed)
            self._completed.clear()
            return bars

    async def flush_all(self) -> List[OHLCVBar]:
        """Force-flush all bars (including partial), e.g. at market close."""
        async with self._lock:
            bars = list(self._completed)
            for partial in self._bars.values():
                if partial.tick_count > 0:
                    bars.append(partial.to_ohlcv())
            self._completed.clear()
            self._bars.clear()
            return bars

    async def store_bars_redis(self, bars: List[OHLCVBar]) -> None:
        """Cache completed bars in Redis sorted sets (score=epoch timestamp)."""
        if not bars:
            return
        try:
            from app.core.redis_client import get_redis
            redis = get_redis()
            pipe = redis.pipeline()
            for bar in bars:
                key = f"ohlcv:1m:{bar.symbol}"
                ts_epoch = datetime.fromisoformat(bar.timestamp).timestamp()
                import json
                pipe.zadd(key, {json.dumps(bar.__dict__): ts_epoch})
                # Keep 1 day of 1-min bars (max ~375 trading minutes)
                pipe.zremrangebyrank(key, 0, -376)
                pipe.expire(key, 86400)
            await pipe.execute()
        except Exception as exc:
            logger.warning("bar_builder.redis_store_failed", err=str(exc))

    @property
    def active_symbols(self) -> int:
        return len(self._bars)
