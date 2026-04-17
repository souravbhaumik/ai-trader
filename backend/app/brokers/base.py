"""Abstract broker adapter interface.

Every broker adapter (yfinance, Angel One, Upstox) must implement this interface.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class Quote:
    symbol: str
    price: float
    prev_close: float
    change: float
    change_pct: float
    volume: int
    high: float
    low: float
    open: float
    timestamp: str  # ISO 8601


@dataclass
class OHLCVBar:
    symbol: str
    timestamp: str  # ISO 8601
    open: float
    high: float
    low: float
    close: float
    volume: int


class BrokerAdapter(ABC):
    """Abstract base class for all data source / broker adapters."""

    broker_name: str = "base"

    # ── Market data ───────────────────────────────────────────────────────────

    @abstractmethod
    async def get_quote(self, symbol: str) -> Optional[Quote]:
        """Fetch a real-time (or 15-min delayed) quote for a single symbol."""

    @abstractmethod
    async def get_quotes_batch(self, symbols: List[str]) -> List[Quote]:
        """Fetch quotes for multiple symbols in one call (most brokers support this)."""

    @abstractmethod
    async def get_history(
        self,
        symbol: str,
        period: str = "1y",
        interval: str = "1d",
    ) -> List[OHLCVBar]:
        """Fetch historical OHLCV bars.

        period: 1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y
        interval: 1m, 5m, 15m, 30m, 1h, 1d, 1wk
        """

    # ── Index data ────────────────────────────────────────────────────────────

    @abstractmethod
    async def get_indices(self) -> List[Quote]:
        """Return quotes for Nifty 50, Sensex, Bank Nifty, IT, Auto, Pharma."""

    # ── Connection management (optional override) ─────────────────────────────

    async def connect(self) -> None:
        """Authenticate / open WebSocket (no-op for REST-only adapters)."""

    async def disconnect(self) -> None:
        """Clean up connections (no-op for REST-only adapters)."""

    def is_credentials_configured(self) -> bool:
        """Return True if API credentials are present and valid."""
        return True  # yfinance needs no credentials; override for broker adapters
