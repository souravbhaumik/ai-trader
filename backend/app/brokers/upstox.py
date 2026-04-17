"""Upstox API v3 broker adapter.

Uses the official Upstox REST API for market data.
Requires: UPSTOX_API_KEY, UPSTOX_API_SECRET, UPSTOX_ACCESS_TOKEN
          (OAuth2 flow — access token generated via web login, stored per user).

Falls back gracefully — returns empty results if credentials are missing.
Full live WebSocket streaming will be implemented in Phase 5.
"""
from __future__ import annotations

from typing import List, Optional

import structlog

from app.brokers.base import BrokerAdapter, OHLCVBar, Quote

logger = structlog.get_logger(__name__)

_UPSTOX_BASE_URL = "https://api.upstox.com/v2"


class UpstoxAdapter(BrokerAdapter):
    broker_name = "upstox"

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        access_token: Optional[str] = None,
    ):
        self._api_key = api_key
        self._api_secret = api_secret
        self._access_token = access_token  # OAuth2 bearer token

    def is_credentials_configured(self) -> bool:
        return bool(self._api_key and self._access_token)

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Accept": "application/json",
        }

    async def get_quote(self, symbol: str) -> Optional[Quote]:
        if not self.is_credentials_configured():
            return None
        try:
            import httpx

            instrument_key = f"NSE_EQ|{symbol.replace('.NS', '')}"
            async with httpx.AsyncClient() as client:
                r = await client.get(
                    f"{_UPSTOX_BASE_URL}/market-quote/quotes",
                    params={"instrument_key": instrument_key},
                    headers=self._headers(),
                    timeout=10,
                )
                r.raise_for_status()
                data = r.json().get("data", {}).get(instrument_key, {})
                ltp = data.get("last_price", 0)
                prev_close = data.get("ohlc", {}).get("close", ltp)
                change = ltp - prev_close
                change_pct = (change / prev_close * 100) if prev_close else 0.0
                from datetime import datetime
                return Quote(
                    symbol=symbol,
                    price=round(float(ltp), 4),
                    prev_close=round(float(prev_close), 4),
                    change=round(change, 4),
                    change_pct=round(change_pct, 2),
                    volume=int(data.get("volume", 0)),
                    high=float(data.get("ohlc", {}).get("high", ltp)),
                    low=float(data.get("ohlc", {}).get("low", ltp)),
                    open=float(data.get("ohlc", {}).get("open", ltp)),
                    timestamp=datetime.utcnow().isoformat(),
                )
        except Exception as e:
            logger.error("upstox_quote_failed", symbol=symbol, error=str(e))
            return None

    async def get_quotes_batch(self, symbols: List[str]) -> List[Quote]:
        if not self.is_credentials_configured():
            return []
        # Upstox supports batch market quotes in one API call
        try:
            import httpx

            keys = ",".join(f"NSE_EQ|{s.replace('.NS', '')}" for s in symbols)
            async with httpx.AsyncClient() as client:
                r = await client.get(
                    f"{_UPSTOX_BASE_URL}/market-quote/quotes",
                    params={"instrument_key": keys},
                    headers=self._headers(),
                    timeout=15,
                )
                r.raise_for_status()
                raw = r.json().get("data", {})
                from datetime import datetime

                quotes: List[Quote] = []
                for sym in symbols:
                    key = f"NSE_EQ|{sym.replace('.NS', '')}"
                    d = raw.get(key, {})
                    if not d:
                        continue
                    ltp = float(d.get("last_price", 0))
                    prev = float(d.get("ohlc", {}).get("close", ltp))
                    change = ltp - prev
                    change_pct = (change / prev * 100) if prev else 0.0
                    quotes.append(Quote(
                        symbol=sym,
                        price=ltp,
                        prev_close=prev,
                        change=round(change, 4),
                        change_pct=round(change_pct, 2),
                        volume=int(d.get("volume", 0)),
                        high=float(d.get("ohlc", {}).get("high", ltp)),
                        low=float(d.get("ohlc", {}).get("low", ltp)),
                        open=float(d.get("ohlc", {}).get("open", ltp)),
                        timestamp=datetime.utcnow().isoformat(),
                    ))
                return quotes
        except Exception as e:
            logger.error("upstox_batch_failed", error=str(e))
            return []

    async def get_history(
        self, symbol: str, period: str = "1y", interval: str = "1d"
    ) -> List[OHLCVBar]:
        # For Phase 2, fallback to yfinance for historical data
        # Upstox historical API needs from_date/to_date — implement fully in Phase 5
        from app.brokers.yfinance_adapter import YFinanceAdapter
        return await YFinanceAdapter().get_history(symbol, period, interval)

    async def get_indices(self) -> List[Quote]:
        from app.brokers.yfinance_adapter import YFinanceAdapter
        return await YFinanceAdapter().get_indices()
