"""yfinance broker adapter — free NSE fallback.

Uses Yahoo Finance (15-min delayed during market hours, free, no API key).
All network calls run in a thread pool to avoid blocking the async event loop.
Retries use tenacity (3 attempts, exponential back-off). A module-level circuit
breaker opens after 5 consecutive failures and half-opens after 60 seconds.
"""
from __future__ import annotations

import asyncio
import threading
import time
from datetime import datetime
from typing import List, Optional

import structlog
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from app.brokers.base import BrokerAdapter, OHLCVBar, Quote

logger = structlog.get_logger(__name__)

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

# Nifty indices as Yahoo Finance symbols
_INDICES = {
    "^NSEI":     "Nifty 50",
    "^BSESN":    "Sensex",
    "^NSEBANK":  "Bank Nifty",
    "NIFTYIT.NS":  "Nifty IT",
    "^CNXPHARMA": "Nifty Pharma",
    "^CNXAUTO":  "Nifty Auto",
}


class YFinanceAdapter(BrokerAdapter):
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

    # ── BrokerAdapter interface ───────────────────────────────────────────────

    async def get_quote(self, symbol: str) -> Optional[Quote]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._sync_quote, symbol)

    async def get_quotes_batch(self, symbols: List[str]) -> List[Quote]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._sync_batch, symbols)

    async def get_history(
        self,
        symbol: str,
        period: str = "1y",
        interval: str = "1d",
    ) -> List[OHLCVBar]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, self._sync_history, symbol, period, interval
        )

    async def get_indices(self) -> List[Quote]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._sync_indices)

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
                timestamp=datetime.utcnow().isoformat(),
            )
        except Exception as e:
            _record_failure()
            logger.warning("yfinance_quote_failed", symbol=ticker, error=str(e))
            return None
        _record_success()

    @_yf_retry
    def _sync_batch(self, symbols: List[str]) -> List[Quote]:
        """Batch download using yfinance.download for efficiency."""
        import pandas as pd
        import yfinance as yf

        _check_circuit()
        tickers = [self._to_ns(s) for s in symbols]
        quotes: List[Quote] = []

        try:
            data = yf.download(
                tickers,
                period="5d",
                interval="1d",
                progress=False,
                auto_adjust=True,
                threads=True,
            )
            if data is None or data.empty:
                return quotes

            close_df = data.get("Close")
            open_df  = data.get("Open")
            high_df  = data.get("High")
            low_df   = data.get("Low")
            vol_df   = data.get("Volume")

            for orig_sym, ticker in zip(symbols, tickers):
                try:
                    if isinstance(close_df, pd.Series):
                        close_s = close_df.dropna()
                        open_s = open_df.dropna()
                        high_s = high_df.dropna()
                        low_s = low_df.dropna()
                        vol_s = vol_df.dropna()
                    else:
                        if ticker not in close_df.columns:
                            continue
                        close_s = close_df[ticker].dropna()
                        open_s = open_df[ticker].dropna()
                        high_s = high_df[ticker].dropna()
                        low_s = low_df[ticker].dropna()
                        vol_s = vol_df[ticker].dropna()

                    if len(close_s) < 2:
                        continue

                    price = self._safe_float(close_s.iloc[-1])
                    prev  = self._safe_float(close_s.iloc[-2])
                    change = price - prev
                    change_pct = (change / prev * 100) if prev else 0.0

                    quotes.append(Quote(
                        symbol=orig_sym,
                        price=price,
                        prev_close=prev,
                        change=round(change, 4),
                        change_pct=round(change_pct, 2),
                        volume=int(vol_s.iloc[-1]) if len(vol_s) else 0,
                        high=self._safe_float(high_s.iloc[-1]) if len(high_s) else price,
                        low=self._safe_float(low_s.iloc[-1]) if len(low_s) else price,
                        open=self._safe_float(open_s.iloc[-1]) if len(open_s) else price,
                        timestamp=datetime.utcnow().isoformat(),
                    ))
                except Exception as e:
                    logger.debug("yfinance_batch_ticker_error", ticker=ticker, error=str(e))

        except Exception as e:
            _record_failure()
            logger.error("yfinance_batch_failed", error=str(e))

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
            logger.error("yfinance_history_failed", symbol=ticker, error=str(e))
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
                    timestamp=datetime.utcnow().isoformat(),
                )
                quotes.append(q)
            except Exception as e:
                _record_failure()
                logger.warning("yfinance_index_failed", index=yf_sym, error=str(e))
        _record_success()
        return quotes
