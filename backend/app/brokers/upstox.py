"""Upstox API v2 broker adapter — full implementation.

Uses the official Upstox REST API for market data and order execution.

OAuth2 flow (one-time setup per user):
  1. User visits the authorization URL (GET /api/v1/auth/upstox/authorize)
  2. Browser redirects to /api/v1/auth/upstox/callback?code=XXX
  3. Backend exchanges the code for access_token + refresh_token
  4. refresh_token stored encrypted in broker_credentials
  5. Daily task at 7:30 AM exchanges refresh_token for a fresh access_token

No yfinance fallback — returns empty results if credentials are missing.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import httpx
import structlog

from app.brokers.base import BrokerAdapter, OHLCVBar, OrderResult, Position, Quote

logger = structlog.get_logger(__name__)

_UPSTOX_BASE_URL = "https://api.upstox.com/v2"
_AUTH_URL        = "https://api.upstox.com/v2/login/authorization/dialog"
_TOKEN_URL       = "https://api.upstox.com/v2/login/authorization/token"
_REQUEST_TIMEOUT = 15

# ── Interval mapping for historical data ──────────────────────────────────────
_INTERVAL_MAP: Dict[str, str] = {
    "1m": "1minute",
    "5m": "5minute",
    "10m": "10minute",
    "15m": "15minute",
    "30m": "30minute",
    "1h": "60minute",
    "1d": "day",
    "1wk": "week",
    "1mo": "month",
}

_PERIOD_DAYS: Dict[str, int] = {
    "1d": 1, "5d": 5, "1mo": 30, "3mo": 90,
    "6mo": 180, "1y": 365, "2y": 730, "5y": 1825,
}

# Upstox index instrument keys
_INDEX_KEYS: Dict[str, str] = {
    "Nifty 50": "NSE_INDEX|Nifty 50",
    "Sensex": "BSE_INDEX|SENSEX",
    "Bank Nifty": "NSE_INDEX|Nifty Bank",
    "Nifty IT": "NSE_INDEX|Nifty IT",
}


class UpstoxAdapter(BrokerAdapter):
    broker_name = "upstox"

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        access_token: Optional[str] = None,
        redirect_uri: Optional[str] = None,
    ):
        self._api_key = api_key
        self._api_secret = api_secret
        self._access_token = access_token  # OAuth2 bearer token
        self._redirect_uri = redirect_uri or "http://localhost:8000/api/v1/auth/upstox/callback"

    def is_credentials_configured(self) -> bool:
        return bool(self._api_key and self._access_token)

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _instrument_key(symbol: str) -> str:
        """Convert a plain symbol like RELIANCE or RELIANCE.NS to Upstox instrument key."""
        clean = symbol.upper().replace(".NS", "").replace(".BO", "")
        return f"NSE_EQ|{clean}"

    # ── OAuth2 helpers ────────────────────────────────────────────────────────

    def get_authorization_url(self, state: Optional[str] = None) -> str:
        """Return the Upstox login URL the user must visit once to authorize."""
        params = {
            "response_type": "code",
            "client_id": self._api_key,
            "redirect_uri": self._redirect_uri,
        }
        if state:
            params["state"] = state
        return f"{_AUTH_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str) -> Dict[str, str]:
        """Exchange a one-time authorization code for access + refresh tokens.

        Returns {"access_token": ..., "refresh_token": ...}.
        Raises RuntimeError on failure.
        """
        async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
            r = await client.post(
                _TOKEN_URL,
                data={
                    "code":          code,
                    "client_id":     self._api_key,
                    "client_secret": self._api_secret,
                    "redirect_uri":  self._redirect_uri,
                    "grant_type":    "authorization_code",
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
            if r.status_code != 200:
                body = r.text
                raise RuntimeError(f"Upstox token exchange failed [{r.status_code}]: {body}")
            data = r.json()
            access_token  = data.get("access_token")
            refresh_token = data.get("refresh_token", "")   # Upstox may not return one
            if not access_token:
                raise RuntimeError(f"No access_token in Upstox response: {data}")
            self._access_token = access_token
            return {"access_token": access_token, "refresh_token": refresh_token}

    async def refresh_access_token(self, refresh_token: str) -> str:
        """Use the refresh_token to get a fresh access_token.

        Note: Upstox v2 does not support a true refresh_token flow — the
        access_token is valid for one trading day. If the refresh fails,
        the caller should mark credentials as needing re-authorization.
        Returns the new access_token string.
        """
        # Upstox does not implement RFC-6749 refresh_token grant.
        # Their access_token is valid until midnight IST.
        # We store it and return it as-is; regeneration requires a new auth code.
        # This method exists for future compatibility and logging.
        logger.info("upstox_access_token_valid_until_midnight")
        return self._access_token or ""

    # ── Market data ───────────────────────────────────────────────────────────

    async def get_quote(self, symbol: str) -> Optional[Quote]:
        if not self.is_credentials_configured():
            return None
        try:
            instrument_key = self._instrument_key(symbol)
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
                r = await client.get(
                    f"{_UPSTOX_BASE_URL}/market-quote/quotes",
                    params={"instrument_key": instrument_key},
                    headers=self._headers(),
                )
                r.raise_for_status()
                # Upstox response keys use ':' but we look up with '|'
                raw = {k.replace(":", "|"): v for k, v in r.json().get("data", {}).items()}
                data = raw.get(instrument_key, {})
                if not data:
                    return None
                return self._parse_quote(symbol, data)
        except Exception as e:
            logger.error("upstox_quote_failed", symbol=symbol, err=str(e))
            return None

    async def get_quotes_batch(self, symbols: List[str]) -> List[Quote]:
        if not self.is_credentials_configured():
            return []
        try:
            keys = ",".join(self._instrument_key(s) for s in symbols)
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
                r = await client.get(
                    f"{_UPSTOX_BASE_URL}/market-quote/quotes",
                    params={"instrument_key": keys},
                    headers=self._headers(),
                )
                r.raise_for_status()
                # Upstox responds with colon-separated keys (NSE_EQ:RELIANCE)
                # but requests use pipe-separated keys (NSE_EQ|RELIANCE)
                raw_data = r.json().get("data", {})
                raw = {k.replace(":", "|"): v for k, v in raw_data.items()}

                quotes: List[Quote] = []
                for sym in symbols:
                    key = self._instrument_key(sym)
                    d = raw.get(key, {})
                    if not d:
                        continue
                    q = self._parse_quote(sym, d)
                    if q:
                        quotes.append(q)
                return quotes
        except Exception as e:
            logger.error("upstox_batch_failed", err=str(e))
            return []

    async def get_history(
        self, symbol: str, period: str = "1y", interval: str = "1d"
    ) -> List[OHLCVBar]:
        if not self.is_credentials_configured():
            return []

        instrument_key = self._instrument_key(symbol)
        upstox_interval = _INTERVAL_MAP.get(interval, "day")
        days_back = _PERIOD_DAYS.get(period, 365)
        now = datetime.now(timezone.utc)
        from_date = (now - timedelta(days=days_back)).strftime("%Y-%m-%d")
        to_date = now.strftime("%Y-%m-%d")

        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
                r = await client.get(
                    f"{_UPSTOX_BASE_URL}/historical-candle/{instrument_key}/{upstox_interval}/{to_date}/{from_date}",
                    headers=self._headers(),
                )
                r.raise_for_status()
                candles = r.json().get("data", {}).get("candles", [])

                bars: List[OHLCVBar] = []
                for candle in candles:
                    # Upstox candle format: [timestamp, open, high, low, close, volume, oi]
                    if len(candle) < 6:
                        continue
                    bars.append(OHLCVBar(
                        symbol=symbol,
                        timestamp=str(candle[0]),
                        open=float(candle[1]),
                        high=float(candle[2]),
                        low=float(candle[3]),
                        close=float(candle[4]),
                        volume=int(candle[5]),
                    ))
                return bars
        except Exception as e:
            logger.error("upstox_history_failed", symbol=symbol, err=str(e))
            return []

    async def get_indices(self) -> List[Quote]:
        if not self.is_credentials_configured():
            return []
        try:
            keys = ",".join(_INDEX_KEYS.values())
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
                r = await client.get(
                    f"{_UPSTOX_BASE_URL}/market-quote/quotes",
                    params={"instrument_key": keys},
                    headers=self._headers(),
                )
                r.raise_for_status()
                # Upstox responds with colon-separated keys (NSE_INDEX:Nifty 50)
                # but requests use pipe-separated keys (NSE_INDEX|Nifty 50)
                raw_data = r.json().get("data", {})
                raw = {k.replace(":", "|"): v for k, v in raw_data.items()}

                quotes: List[Quote] = []
                for display_name, key in _INDEX_KEYS.items():
                    d = raw.get(key, {})
                    if not d:
                        continue
                    q = self._parse_quote(display_name, d)
                    if q:
                        quotes.append(q)
                return quotes
        except Exception as e:
            logger.error("upstox_indices_failed", err=str(e))
            return []

    # ── Order execution ───────────────────────────────────────────────────────

    async def place_order(
        self,
        *,
        symbol: str,
        direction: str,
        qty: int,
        order_type: str = "MARKET",
        product_type: str = "DELIVERY",
        price: float = 0.0,
        stop_loss: float = 0.0,
        target: float = 0.0,
        order_tag: str = "",
    ) -> OrderResult:
        if not self.is_credentials_configured():
            raise RuntimeError("Upstox not connected — check credentials in Settings")

        instrument_key = self._instrument_key(symbol)

        # Map product type: DELIVERY→D, INTRADAY→I
        product_map = {"DELIVERY": "D", "INTRADAY": "I", "D": "D", "I": "I"}
        upstox_product = product_map.get(product_type.upper(), "D")

        # Map order type
        order_type_upper = order_type.upper()
        upstox_order_type = "MARKET" if order_type_upper == "MARKET" else "LIMIT"

        payload = {
            "quantity": qty,
            "product": upstox_product,
            "validity": "DAY",
            "price": price if upstox_order_type == "LIMIT" else 0,
            "tag": order_tag[:20] if order_tag else "",
            "instrument_token": instrument_key,
            "order_type": upstox_order_type,
            "transaction_type": direction.upper(),
            "disclosed_quantity": 0,
            "trigger_price": 0,
            "is_amo": False,
        }

        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
                r = await client.post(
                    f"{_UPSTOX_BASE_URL}/order/place",
                    json=payload,
                    headers=self._headers(),
                )
                r.raise_for_status()
                result = r.json()

                if result.get("status") == "success":
                    order_id = str(result.get("data", {}).get("order_id", ""))
                    return OrderResult(
                        broker_order_id=order_id,
                        status="PENDING",
                        symbol=symbol,
                        exchange="NSE",
                        direction=direction.upper(),
                        qty=qty,
                        order_type=upstox_order_type,
                        product_type=product_type.upper(),
                        price=price,
                        message=result.get("message", ""),
                        raw=result,
                    )
                msg = result.get("errors", [{}])[0].get("message", "Unknown error") if result.get("errors") else "Order rejected"
                raise RuntimeError(f"Upstox order rejected: {msg}")
        except RuntimeError:
            raise
        except Exception as e:
            logger.error("upstox_place_order_error", err=str(e))
            raise RuntimeError(f"Upstox order error: {e}") from e

    async def cancel_order(self, broker_order_id: str, variety: str = "NORMAL") -> bool:
        if not self.is_credentials_configured():
            return False
        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
                r = await client.delete(
                    f"{_UPSTOX_BASE_URL}/order/cancel",
                    params={"order_id": broker_order_id},
                    headers=self._headers(),
                )
                r.raise_for_status()
                result = r.json()
                return result.get("status") == "success"
        except Exception as e:
            logger.error("upstox_cancel_order_error", err=str(e))
            return False

    async def get_order_status(self, broker_order_id: str) -> Optional[OrderResult]:
        if not self.is_credentials_configured():
            return None
        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
                r = await client.get(
                    f"{_UPSTOX_BASE_URL}/order/details",
                    params={"order_id": broker_order_id},
                    headers=self._headers(),
                )
                r.raise_for_status()
                result = r.json()
                if result.get("status") == "success" and result.get("data"):
                    return self._parse_order(result["data"])
        except Exception as e:
            logger.error("upstox_order_status_error", err=str(e))
        return None

    async def get_positions(self) -> List[Position]:
        if not self.is_credentials_configured():
            return []
        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
                r = await client.get(
                    f"{_UPSTOX_BASE_URL}/portfolio/short-term-positions",
                    headers=self._headers(),
                )
                r.raise_for_status()
                result = r.json()
                if result.get("status") == "success":
                    return [
                        self._parse_position(p)
                        for p in (result.get("data") or [])
                    ]
        except Exception as e:
            logger.error("upstox_positions_error", err=str(e))
        return []

    async def get_holdings(self) -> List[Position]:
        if not self.is_credentials_configured():
            return []
        try:
            async with httpx.AsyncClient(timeout=_REQUEST_TIMEOUT) as client:
                r = await client.get(
                    f"{_UPSTOX_BASE_URL}/portfolio/long-term-holdings",
                    headers=self._headers(),
                )
                r.raise_for_status()
                result = r.json()
                if result.get("status") == "success":
                    return [
                        self._parse_holding(h)
                        for h in (result.get("data") or [])
                    ]
        except Exception as e:
            logger.error("upstox_holdings_error", err=str(e))
        return []

    # ── Parsing helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _parse_quote(symbol: str, data: dict) -> Optional[Quote]:
        try:
            ltp = float(data.get("last_price", 0) or 0)
            ohlc = data.get("ohlc", {})
            prev_close = float(ohlc.get("close", 0) or 0)
            # After market hours Upstox returns last_price=0 — use ohlc.close as price
            price = ltp if ltp > 0 else prev_close
            if price == 0:
                return None  # no data at all, skip
            change = price - prev_close if ltp > 0 else 0.0
            change_pct = (change / prev_close * 100) if prev_close and ltp > 0 else 0.0
            return Quote(
                symbol=symbol,
                price=round(float(price), 4),
                prev_close=round(float(prev_close), 4),
                change=round(change, 4),
                change_pct=round(change_pct, 2),
                volume=int(data.get("volume", 0) or 0),
                high=float(ohlc.get("high", price) or price),
                low=float(ohlc.get("low", price) or price),
                open=float(ohlc.get("open", price) or price),
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
        except Exception as e:
            logger.error("upstox_parse_quote_error", err=str(e))
            return None

    @staticmethod
    def _parse_order(data: dict) -> OrderResult:
        # Map Upstox status to our standard statuses
        status_map = {
            "complete": "COMPLETE",
            "rejected": "REJECTED",
            "cancelled": "CANCELLED",
            "open": "OPEN",
            "pending": "PENDING",
            "trigger pending": "PENDING",
        }
        raw_status = str(data.get("status", "")).lower()
        status = status_map.get(raw_status, raw_status.upper())

        return OrderResult(
            broker_order_id=str(data.get("order_id", "")),
            status=status,
            symbol=str(data.get("trading_symbol", "")),
            exchange=str(data.get("exchange", "NSE")),
            direction=str(data.get("transaction_type", "BUY")).upper(),
            qty=int(data.get("quantity", 0) or 0),
            order_type=str(data.get("order_type", "MARKET")).upper(),
            product_type=str(data.get("product", "D")).upper(),
            price=float(data.get("price", 0) or 0),
            filled_qty=int(data.get("filled_quantity", 0) or 0),
            avg_fill_price=float(data.get("average_price", 0) or 0),
            message=str(data.get("status_message", "")),
            raw=data,
        )

    @staticmethod
    def _parse_position(p: dict) -> Position:
        qty = int(p.get("quantity", 0) or 0)
        avg = float(p.get("average_price", 0) or 0)
        ltp = float(p.get("last_price", 0) or 0)
        pnl = float(p.get("pnl", 0) or 0)
        cost = avg * abs(qty) if avg and qty else 1
        return Position(
            symbol=str(p.get("trading_symbol", "")),
            exchange=str(p.get("exchange", "NSE")),
            product_type=str(p.get("product", "D")).upper(),
            direction="BUY" if qty >= 0 else "SELL",
            qty=abs(qty),
            avg_buy_price=avg,
            ltp=ltp,
            pnl=pnl,
            pnl_pct=round(pnl / cost * 100, 2) if cost else 0.0,
            symbol_token=str(p.get("instrument_token", "")),
        )

    @staticmethod
    def _parse_holding(h: dict) -> Position:
        qty = int(h.get("quantity", 0) or 0)
        avg = float(h.get("average_price", 0) or 0)
        ltp = float(h.get("last_price", 0) or 0)
        pnl = (ltp - avg) * qty
        cost = avg * qty if avg and qty else 1
        return Position(
            symbol=str(h.get("trading_symbol", "")),
            exchange=str(h.get("exchange", "NSE")),
            product_type="DELIVERY",
            direction="BUY",
            qty=qty,
            avg_buy_price=avg,
            ltp=ltp,
            pnl=round(pnl, 2),
            pnl_pct=round(pnl / cost * 100, 2) if cost else 0.0,
            symbol_token=str(h.get("instrument_token", "")),
        )
