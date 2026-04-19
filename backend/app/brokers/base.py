"""Abstract broker adapter interface.

Every broker adapter (yfinance, Angel One, Upstox) must implement this interface.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


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


@dataclass
class OrderResult:
    """Result of a place_order call."""
    broker_order_id: str
    status: str          # "PENDING" | "OPEN" | "COMPLETE" | "CANCELLED" | "REJECTED"
    symbol: str
    exchange: str
    direction: str       # "BUY" | "SELL"
    qty: int
    order_type: str      # "MARKET" | "LIMIT"
    product_type: str    # "DELIVERY" | "INTRADAY"
    price: float
    filled_qty: int = 0
    avg_fill_price: float = 0.0
    message: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass
class Position:
    """An open live position."""
    symbol: str
    exchange: str
    product_type: str    # "DELIVERY" | "INTRADAY"
    direction: str       # "BUY" | "SELL" (net)
    qty: int
    avg_buy_price: float
    ltp: float
    pnl: float
    pnl_pct: float
    symbol_token: str = ""


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

    # ── Order execution (optional — only live brokers implement these) ────────

    async def place_order(
        self,
        *,
        symbol: str,
        direction: str,        # "BUY" | "SELL"
        qty: int,
        order_type: str = "MARKET",   # "MARKET" | "LIMIT"
        product_type: str = "DELIVERY",  # "DELIVERY" | "INTRADAY"
        price: float = 0.0,
        stop_loss: float = 0.0,
        target: float = 0.0,
        order_tag: str = "",   # unique client-generated ID for idempotency
    ) -> OrderResult:
        """Place a live order. Raises NotImplementedError if broker doesn't support it."""
        raise NotImplementedError(f"{self.broker_name} does not support order placement")

    async def cancel_order(self, broker_order_id: str, variety: str = "NORMAL") -> bool:
        """Cancel an open order. Returns True on success."""
        raise NotImplementedError(f"{self.broker_name} does not support cancel_order")

    async def get_order_status(self, broker_order_id: str) -> Optional[OrderResult]:
        """Fetch current status of a specific order."""
        raise NotImplementedError(f"{self.broker_name} does not support get_order_status")

    async def get_positions(self) -> List[Position]:
        """Return all open intraday/delivery positions."""
        raise NotImplementedError(f"{self.broker_name} does not support get_positions")

    async def get_holdings(self) -> List[Position]:
        """Return long-term delivery holdings."""
        raise NotImplementedError(f"{self.broker_name} does not support get_holdings")
