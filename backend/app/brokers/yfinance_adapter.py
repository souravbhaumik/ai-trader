"""yfinance broker adapter — historical OHLCV data only.

This adapter's ONLY active function is get_history(), which fetches
historical OHLCV bars from Yahoo Finance via yf.download(). It is the
sole source for historical data in this codebase.

Live price methods (get_quote, get_quotes_batch, get_indices) are
intentionally disabled — they return None / empty list. All live prices
are sourced from NSEIndiaAdapter with broker API as fallback.

The 15-minute delay of Yahoo Finance is irrelevant for historical
backfill, ML training datasets, and chart rendering.

Retries use tenacity (3 attempts, exponential back-off). A module-level
circuit breaker opens after 5 consecutive failures and half-opens after
60 seconds.
"""
from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional

import structlog
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from app.brokers.base import BrokerAdapter, OHLCVBar, Quote

logger = structlog.get_logger(__name__)

# IST timezone — quote timestamps use IST wall-clock
_IST = timezone(timedelta(hours=5, minutes=30))


# ── Circuit breaker state (module-level, shared across adapter instances) ─────
_CB_LOCK            = threading.Lock()
_CB_FAILURES        = 0
_CB_OPEN_UNTIL: float = 0.0      # epoch seconds; 0 means closed
_CB_THRESHOLD       = 5          # consecutive failures to trip
_CB_RECOVERY_SECS   = 60.0       # half-open window


def _check_circuit():
    """Raise RuntimeError if the circuit is open (too many recent failures)."""
    global _CB_OPEN_UNTIL
    with _CB_LOCK:
        if _CB_OPEN_UNTIL and time.monotonic() < _CB_OPEN_UNTIL:
            raise RuntimeError(
                f"yfinance circuit open — retrying after {_CB_OPEN_UNTIL - time.monotonic():.0f}s"
            )


def _record_success():
    global _CB_FAILURES, _CB_OPEN_UNTIL
    with _CB_LOCK:
        _CB_FAILURES   = 0
        _CB_OPEN_UNTIL = 0.0


def _record_failure():
    global _CB_FAILURES, _CB_OPEN_UNTIL
    with _CB_LOCK:
        _CB_FAILURES += 1
        if _CB_FAILURES >= _CB_THRESHOLD:
            _CB_OPEN_UNTIL = time.monotonic() + _CB_RECOVERY_SECS
            logger.warning(
                "yfinance_circuit_opened",
                failures=_CB_FAILURES,
                recovery_secs=_CB_RECOVERY_SECS,
            )


_yf_retry = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=2, max=10),
    retry=retry_if_exception_type(Exception),
    reraise=True,
)

# Nifty indices as Yahoo Finance symbols (kept for reference, not used actively)
_INDICES = {
    "^NSEI":      "Nifty 50",
    "^BSESN":     "Sensex",
    "^NSEBANK":   "Bank Nifty",
    "^CNXIT":     "Nifty IT",
    "^CNXPHARMA": "Nifty Pharma",
    "^CNXAUTO":   "Nifty Auto",
}


class YFinanceAdapter(BrokerAdapter):
    """Historical OHLCV data only — live methods disabled.

    Use NSEIndiaAdapter (via price_service) for all live prices.
    """

    broker_name = "yfinance"

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _to_ns(symbol: str) -> str:
        """Append .NS suffix if not already present."""
        if symbol.startswith("^") or "." in symbol:
            return symbol
        return f"{symbol}.NS"

    @staticmethod
    def _safe_float(val) -> float:
        try:
            return round(float(val), 4)
        except Exception:
            return 0.0

    # ── Live methods — DISABLED ───────────────────────────────────────────────

    async def get_quote(self, symbol: str) -> Optional[Quote]:
        """Disabled — NSEIndiaAdapter handles all live quotes."""
        return None

    async def get_quotes_batch(self, symbols: List[str]) -> List[Quote]:
        """Disabled — NSEIndiaAdapter handles all live quotes."""
        return []

    async def get_indices(self) -> List[Quote]:
        """Disabled — NSEIndiaAdapter handles all live index quotes."""
        return []

    # ── Sync implementations (run in thread pool) ─────────────────────────────

    @_yf_retry
    def _sync_quote(self, symbol: str) -> Optional[Quote]:
        import yfinance as yf

        _check_circuit()
        ticker = self._to_ns(symbol)
        try:
            info = yf.Ticker(ticker).fast_info
            price = self._safe_float(info.last_price)
            prev = self._safe_float(info.previous_close) or price
            change = price - prev
            change_pct = (change / prev * 100) if prev else 0.0
            _record_success()
            return Quote(
                symbol=symbol,
                price=price,
                prev_close=prev,
                change=round(change, 4),
                change_pct=round(change_pct, 2),
                volume=int(info.three_month_average_volume or 0),
                high=self._safe_float(info.day_high),
                low=self._safe_float(info.day_low),
                open=self._safe_float(info.open),
                timestamp=datetime.now(_IST).isoformat(),
            )
        except Exception as e:
            _record_failure()
            logger.warning("yfinance_quote_failed", symbol=ticker, err=str(e))
            return None

    def _sync_batch(self, symbols: List[str]) -> List[Quote]:
        """Fetch quotes for multiple symbols via individual fast_info calls.

        Uses concurrent.futures.ThreadPoolExecutor to fetch symbols in parallel.
        This avoids yfinance.download() which suffers from Yahoo Finance rate
        limiting and a multi-index column ambiguity bug in recent yfinance versions.
        Each fast_info call is independent so a failure on one ticker does not
        affect the others.
        """
        import yfinance as yf
        from concurrent.futures import ThreadPoolExecutor, as_completed

        _check_circuit()
        quotes: List[Quote] = []

        def _fetch_one(orig_sym: str) -> Optional[Quote]:
            ticker = self._to_ns(orig_sym)
            try:
                fi = yf.Ticker(ticker).fast_info
                price = self._safe_float(fi.last_price)
                prev  = self._safe_float(fi.previous_close) or price
                if not price:
                    return None
                change = price - prev
                change_pct = (change / prev * 100) if prev else 0.0
                return Quote(
                    symbol=orig_sym,
                    price=price,
                    prev_close=prev,
                    change=round(change, 4),
                    change_pct=round(change_pct, 2),
                    volume=int(fi.three_month_average_volume or 0),
                    high=self._safe_float(fi.day_high) or price,
                    low=self._safe_float(fi.day_low) or price,
                    open=self._safe_float(fi.open) or price,
                    timestamp=datetime.now(_IST).isoformat(),
                )
            except Exception as e:
                logger.debug("yfinance_batch_ticker_error", ticker=ticker, err=str(e))
                return None

        try:
            # Cap concurrency at 10 to avoid hammering Yahoo Finance
            with ThreadPoolExecutor(max_workers=10) as pool:
                futures = {pool.submit(_fetch_one, sym): sym for sym in symbols}
                for fut in as_completed(futures):
                    result = fut.result()
                    if result is not None:
                        quotes.append(result)
        except Exception as e:
            _record_failure()
            logger.error("yfinance_batch_failed", err=str(e))
            return quotes

        _record_success()
        return quotes

    @_yf_retry
    def _sync_history(self, symbol: str, period: str, interval: str) -> List[OHLCVBar]:
        import yfinance as yf

        _check_circuit()
        ticker = self._to_ns(symbol)
        bars: List[OHLCVBar] = []
        try:
            df = yf.download(
                ticker,
                period=period,
                interval=interval,
                progress=False,
                auto_adjust=True,
            )
            if df is None or df.empty:
                return bars

            for ts, row in df.iterrows():
                bars.append(OHLCVBar(
                    symbol=symbol,
                    timestamp=ts.isoformat(),
                    open=self._safe_float(row.get("Open", 0)),
                    high=self._safe_float(row.get("High", 0)),
                    low=self._safe_float(row.get("Low", 0)),
                    close=self._safe_float(row.get("Close", 0)),
                    volume=int(row.get("Volume", 0)),
                ))
        except Exception as e:
            _record_failure()
            logger.error("yfinance_history_failed", symbol=ticker, err=str(e))
        else:
            _record_success()
        return bars

    @_yf_retry
    def _sync_indices(self) -> List[Quote]:
        import yfinance as yf

        _check_circuit()
        quotes: List[Quote] = []
        for yf_sym, display_name in _INDICES.items():
            try:
                info = yf.Ticker(yf_sym).fast_info
                price = self._safe_float(info.last_price)
                prev  = self._safe_float(info.previous_close) or price
                change = price - prev
                change_pct = (change / prev * 100) if prev else 0.0
                q = Quote(
                    symbol=display_name,
                    price=price,
                    prev_close=prev,
                    change=round(change, 4),
                    change_pct=round(change_pct, 2),
                    volume=0,
                    high=self._safe_float(info.day_high),
                    low=self._safe_float(info.day_low),
                    open=self._safe_float(info.open),
                    timestamp=datetime.now(_IST).isoformat(),
                )
                quotes.append(q)
            except Exception as e:
                _record_failure()
                logger.warning("yfinance_index_failed", index=yf_sym, err=str(e))
        _record_success()
        return quotes
