"""Angel One SmartAPI broker adapter — full implementation.

Uses the official `smartapi-python` SDK.
Credentials come from the per-user broker_credentials table (decrypted by factory.py).

Market data:
  - get_quote()         → marketData (FULL)
  - get_quotes_batch()  → marketData (FULL) with exchange-token dict
  - get_history()       → getCandleData

Order execution:
  - place_order()       → placeOrder
  - cancel_order()      → cancelOrder
  - get_order_status()  → individual order from getOrderBook
  - get_positions()     → position()
  - get_holdings()      → holding()

Falls back gracefully to yfinance for data if credentials are missing
or authentication fails, so paper-trading users are never broken.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.brokers.base import BrokerAdapter, OHLCVBar, OrderResult, Position, Quote

logger = logging.getLogger(__name__)


# ── Interval mapping ──────────────────────────────────────────────────────────

_INTERVAL_MAP: Dict[str, str] = {
    "1m":  "ONE_MINUTE",
    "3m":  "THREE_MINUTE",
    "5m":  "FIVE_MINUTE",
    "10m": "TEN_MINUTE",
    "15m": "FIFTEEN_MINUTE",
    "30m": "THIRTY_MINUTE",
    "1h":  "ONE_HOUR",
    "1d":  "ONE_DAY",
    "1wk": "ONE_DAY",
}

_PERIOD_MAP: Dict[str, int] = {
    "1d": 1, "5d": 5, "1mo": 30, "3mo": 90,
    "6mo": 180, "1y": 365, "2y": 730, "5y": 1825,
}


class AngelOneAdapter(BrokerAdapter):
    broker_name = "angel_one"

    def __init__(
        self,
        api_key: Optional[str] = None,
        client_id: Optional[str] = None,
        password: Optional[str] = None,       # MPIN
        totp_secret: Optional[str] = None,
    ):
        self._api_key = api_key
        self._client_id = client_id
        self._password = password
        self._totp_secret = totp_secret
        self._smart_api: Any = None
        self._auth_token: Optional[str] = None

    # ── Credential check ──────────────────────────────────────────────────────

    def is_credentials_configured(self) -> bool:
        return bool(
            self._api_key
            and self._client_id
            and self._password
            and self._totp_secret
        )

    # ── Connection lifecycle ──────────────────────────────────────────────────

    async def connect(self) -> None:
        if not self.is_credentials_configured():
            logger.info("angel_one_not_configured")
            return
        try:
            import pyotp
            from SmartApi import SmartConnect  # type: ignore

            self._smart_api = SmartConnect(api_key=self._api_key)
            totp = pyotp.TOTP(self._totp_secret).now()
            data = self._smart_api.generateSession(self._client_id, self._password, totp)
            if data.get("status"):
                self._auth_token = data["data"]["jwtToken"]
                logger.info("angel_one_connected", client_id=self._client_id)
            else:
                logger.error("angel_one_auth_failed", response=data)
                self._smart_api = None
        except Exception as e:  # noqa: BLE001
            logger.error("angel_one_connect_error", error=str(e))
            self._smart_api = None

    async def disconnect(self) -> None:
        if self._smart_api and self._client_id:
            try:
                self._smart_api.terminateSession(self._client_id)
            except Exception:  # noqa: BLE001
                pass
            self._smart_api = None

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _get_token_info(self, symbol: str) -> Optional[Dict]:
        from app.services.angel_symbol_master import get_token
        return await get_token(symbol)

    async def _run_sync(self, fn, *args, **kwargs):
        """Run a synchronous SmartAPI call in a thread pool."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))

    async def _market_data(self, exchange_tokens: Dict[str, List[str]], mode: str = "FULL"):
        """Call marketData API and return fetched list or []."""
        if not self._smart_api:
            return []
        try:
            result = await self._run_sync(self._smart_api.marketData, mode, exchange_tokens)
            if result and result.get("status"):
                return result.get("data", {}).get("fetched", [])
        except Exception as e:  # noqa: BLE001
            logger.error("angel_one_market_data_error", error=str(e))
        return []

    # ── Market data ───────────────────────────────────────────────────────────

    async def get_quote(self, symbol: str) -> Optional[Quote]:
        if not self._smart_api:
            return await self._yf_quote(symbol)

        tok_info = await self._get_token_info(symbol)
        if not tok_info:
            logger.warning("angel_one_token_not_found", symbol=symbol)
            return await self._yf_quote(symbol)

        fetched = await self._market_data({tok_info["exchange"]: [tok_info["token"]]}, mode="FULL")
        if not fetched:
            return await self._yf_quote(symbol)

        return self._parse_quote(symbol, fetched[0])

    async def get_quotes_batch(self, symbols: List[str]) -> List[Quote]:
        if not self._smart_api:
            return await self._yf_batch(symbols)

        exchange_tokens: Dict[str, List[str]] = {}
        token_to_sym: Dict[str, str] = {}

        for sym in symbols:
            tok_info = await self._get_token_info(sym)
            if tok_info:
                exc = tok_info["exchange"]
                tok = tok_info["token"]
                exchange_tokens.setdefault(exc, []).append(tok)
                token_to_sym[tok] = sym

        if not exchange_tokens:
            return await self._yf_batch(symbols)

        fetched = await self._market_data(exchange_tokens, mode="FULL")
        quotes: List[Quote] = []
        for item in fetched:
            tok = str(item.get("symbolToken", ""))
            sym = token_to_sym.get(tok, item.get("tradingSymbol", "UNKNOWN"))
            q = self._parse_quote(sym, item)
            if q:
                quotes.append(q)
        return quotes

    async def get_history(
        self, symbol: str, period: str = "1y", interval: str = "1d"
    ) -> List[OHLCVBar]:
        if not self._smart_api:
            return await self._yf_history(symbol, period, interval)

        tok_info = await self._get_token_info(symbol)
        if not tok_info:
            return await self._yf_history(symbol, period, interval)

        ao_interval = _INTERVAL_MAP.get(interval, "ONE_DAY")
        days_back = _PERIOD_MAP.get(period, 365)
        now = datetime.now(timezone.utc)
        from_dt = now - timedelta(days=days_back)
        fmt = "%Y-%m-%d %H:%M"

        params = {
            "exchange": tok_info["exchange"],
            "symboltoken": tok_info["token"],
            "interval": ao_interval,
            "fromdate": from_dt.strftime(fmt),
            "todate": now.strftime(fmt),
        }

        try:
            result = await self._run_sync(self._smart_api.getCandleData, params)
            if result and result.get("status"):
                bars = []
                for row in result.get("data", []):
                    if len(row) < 6:
                        continue
                    bars.append(
                        OHLCVBar(
                            symbol=symbol,
                            timestamp=str(row[0]),
                            open=float(row[1]),
                            high=float(row[2]),
                            low=float(row[3]),
                            close=float(row[4]),
                            volume=int(row[5]),
                        )
                    )
                return bars
        except Exception as e:  # noqa: BLE001
            logger.error("angel_one_history_error", symbol=symbol, error=str(e))

        return await self._yf_history(symbol, period, interval)

    async def get_indices(self) -> List[Quote]:
        if not self._smart_api:
            return await self._yf_indices()
        return await self.get_quotes_batch(["NIFTY50", "BANKNIFTY", "SENSEX", "NIFTYIT"])

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
    ) -> OrderResult:
        if not self._smart_api:
            raise RuntimeError("Angel One not connected — check credentials in Settings")

        tok_info = await self._get_token_info(symbol)
        if not tok_info:
            raise ValueError(f"Symbol not found in Angel One instrument master: {symbol}")

        ao_order_type = "MARKET" if order_type.upper() == "MARKET" else "LIMIT"

        params = {
            "variety": "NORMAL",
            "tradingsymbol": tok_info["tradingsymbol"],
            "symboltoken": tok_info["token"],
            "transactiontype": direction.upper(),
            "exchange": tok_info["exchange"],
            "ordertype": ao_order_type,
            "producttype": product_type.upper(),
            "duration": "DAY",
            "price": str(price) if ao_order_type == "LIMIT" else "0",
            "squareoff": "0",
            "stoploss": "0",
            "quantity": str(qty),
        }

        try:
            result = await self._run_sync(self._smart_api.placeOrder, params)
            logger.info("angel_one_place_order", symbol=symbol, result=result)

            if result and result.get("status"):
                broker_order_id = str(result.get("data", {}).get("orderid", ""))
                return OrderResult(
                    broker_order_id=broker_order_id,
                    status="PENDING",
                    symbol=symbol,
                    exchange=tok_info["exchange"],
                    direction=direction.upper(),
                    qty=qty,
                    order_type=ao_order_type,
                    product_type=product_type.upper(),
                    price=price,
                    message=result.get("message", ""),
                    raw=result,
                )
            msg = result.get("message", "Unknown error") if result else "No response"
            raise RuntimeError(f"Angel One order rejected: {msg}")
        except RuntimeError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.error("angel_one_place_order_error", error=str(e))
            raise RuntimeError(f"Angel One order error: {e}") from e

    async def cancel_order(self, broker_order_id: str, variety: str = "NORMAL") -> bool:
        if not self._smart_api:
            return False
        try:
            result = await self._run_sync(
                self._smart_api.cancelOrder, variety, broker_order_id
            )
            return bool(result and result.get("status"))
        except Exception as e:  # noqa: BLE001
            logger.error("angel_one_cancel_order_error", error=str(e))
            return False

    async def get_order_status(self, broker_order_id: str) -> Optional[OrderResult]:
        if not self._smart_api:
            return None
        try:
            result = await self._run_sync(self._smart_api.getOrderBook)
            if not (result and result.get("status")):
                return None
            for order in result.get("data", []) or []:
                if str(order.get("orderid", "")) == broker_order_id:
                    return self._parse_order(order)
        except Exception as e:  # noqa: BLE001
            logger.error("angel_one_order_status_error", error=str(e))
        return None

    async def get_positions(self) -> List[Position]:
        if not self._smart_api:
            return []
        try:
            result = await self._run_sync(self._smart_api.position)
            if result and result.get("status"):
                return [self._parse_position(p) for p in (result.get("data") or [])]
        except Exception as e:  # noqa: BLE001
            logger.error("angel_one_positions_error", error=str(e))
        return []

    async def get_holdings(self) -> List[Position]:
        if not self._smart_api:
            return []
        try:
            result = await self._run_sync(self._smart_api.holding)
            if result and result.get("status"):
                return [self._parse_holding(h) for h in (result.get("data") or [])]
        except Exception as e:  # noqa: BLE001
            logger.error("angel_one_holdings_error", error=str(e))
        return []

    # ── Parsing helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _parse_quote(symbol: str, item: dict) -> Optional[Quote]:
        try:
            ltp = float(item.get("ltp", 0) or 0)
            prev_close = float(item.get("close", 0) or 0)
            change = ltp - prev_close
            change_pct = (change / prev_close * 100) if prev_close else 0.0
            return Quote(
                symbol=symbol,
                price=ltp,
                prev_close=prev_close,
                change=round(change, 2),
                change_pct=round(change_pct, 2),
                volume=int(item.get("tradedVolume", 0) or 0),
                high=float(item.get("high", 0) or 0),
                low=float(item.get("low", 0) or 0),
                open=float(item.get("open", 0) or 0),
                timestamp=datetime.now(timezone.utc).isoformat(),
            )
        except Exception as e:  # noqa: BLE001
            logger.error("angel_one_parse_quote_error", error=str(e))
            return None

    @staticmethod
    def _parse_order(order: dict) -> OrderResult:
        return OrderResult(
            broker_order_id=str(order.get("orderid", "")),
            status=str(order.get("status", "UNKNOWN")).upper(),
            symbol=str(order.get("tradingsymbol", "")),
            exchange=str(order.get("exchange", "NSE")),
            direction=str(order.get("transactiontype", "BUY")).upper(),
            qty=int(order.get("quantity", 0) or 0),
            order_type=str(order.get("ordertype", "MARKET")).upper(),
            product_type=str(order.get("producttype", "DELIVERY")).upper(),
            price=float(order.get("price", 0) or 0),
            filled_qty=int(order.get("filledshares", 0) or 0),
            avg_fill_price=float(order.get("averageprice", 0) or 0),
            message=str(order.get("text", "")),
            raw=order,
        )

    @staticmethod
    def _parse_position(p: dict) -> Position:
        qty = int(p.get("netqty", 0) or 0)
        avg = float(p.get("netprice", 0) or 0)
        ltp = float(p.get("ltp", 0) or 0)
        pnl = float(p.get("pnl", 0) or 0)
        cost = avg * abs(qty) if avg and qty else 1
        return Position(
            symbol=str(p.get("tradingsymbol", "")),
            exchange=str(p.get("exchange", "NSE")),
            product_type=str(p.get("producttype", "INTRADAY")).upper(),
            direction="BUY" if qty >= 0 else "SELL",
            qty=abs(qty),
            avg_buy_price=avg,
            ltp=ltp,
            pnl=pnl,
            pnl_pct=round(pnl / cost * 100, 2) if cost else 0.0,
            symbol_token=str(p.get("symboltoken", "")),
        )

    @staticmethod
    def _parse_holding(h: dict) -> Position:
        qty = int(h.get("quantity", 0) or 0)
        avg = float(h.get("averageprice", 0) or 0)
        ltp = float(h.get("ltp", 0) or 0)
        pnl = (ltp - avg) * qty
        cost = avg * qty if avg and qty else 1
        return Position(
            symbol=str(h.get("tradingsymbol", "")),
            exchange=str(h.get("exchange", "NSE")),
            product_type="DELIVERY",
            direction="BUY",
            qty=qty,
            avg_buy_price=avg,
            ltp=ltp,
            pnl=round(pnl, 2),
            pnl_pct=round(pnl / cost * 100, 2) if cost else 0.0,
            symbol_token=str(h.get("symboltoken", "")),
        )

    # ── yfinance fallbacks ────────────────────────────────────────────────────

    @staticmethod
    async def _yf_quote(symbol: str) -> Optional[Quote]:
        from app.brokers.yfinance_adapter import YFinanceAdapter
        return await YFinanceAdapter().get_quote(symbol)

    @staticmethod
    async def _yf_batch(symbols: List[str]) -> List[Quote]:
        from app.brokers.yfinance_adapter import YFinanceAdapter
        return await YFinanceAdapter().get_quotes_batch(symbols)

    @staticmethod
    async def _yf_history(symbol: str, period: str, interval: str) -> List[OHLCVBar]:
        from app.brokers.yfinance_adapter import YFinanceAdapter
        return await YFinanceAdapter().get_history(symbol, period, interval)

    @staticmethod
    async def _yf_indices() -> List[Quote]:
        from app.brokers.yfinance_adapter import YFinanceAdapter
        return await YFinanceAdapter().get_indices()

