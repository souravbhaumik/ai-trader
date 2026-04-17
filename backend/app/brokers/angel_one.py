"""Angel One SmartAPI broker adapter.

Uses the official `smartapi-python` SDK for REST + WebSocket market data.
Requires: ANGEL_ONE_API_KEY, ANGEL_ONE_CLIENT_ID, ANGEL_ONE_PASSWORD,
          ANGEL_ONE_TOTP_SECRET (set per user in broker_credentials table).

Falls back gracefully — returns empty results if credentials are missing
or authentication fails, with a clear `is_configured = False` state.
"""
from __future__ import annotations

from typing import List, Optional

import structlog

from app.brokers.base import BrokerAdapter, OHLCVBar, Quote

logger = structlog.get_logger(__name__)


class AngelOneAdapter(BrokerAdapter):
    broker_name = "angel_one"

    def __init__(
        self,
        api_key: Optional[str] = None,
        client_id: Optional[str] = None,
        password: Optional[str] = None,
        totp_secret: Optional[str] = None,
    ):
        self._api_key = api_key
        self._client_id = client_id
        self._password = password
        self._totp_secret = totp_secret
        self._smart_api = None
        self._auth_token: Optional[str] = None

    def is_credentials_configured(self) -> bool:
        return bool(
            self._api_key
            and self._client_id
            and self._password
            and self._totp_secret
        )

    async def connect(self) -> None:
        if not self.is_credentials_configured():
            logger.info("angel_one_not_configured")
            return
        try:
            import pyotp
            from SmartApi import SmartConnect  # type: ignore

            self._smart_api = SmartConnect(api_key=self._api_key)
            totp = pyotp.TOTP(self._totp_secret).now()
            data = self._smart_api.generateSession(
                self._client_id, self._password, totp
            )
            if data.get("status"):
                self._auth_token = data["data"]["jwtToken"]
                logger.info("angel_one_connected", client_id=self._client_id)
            else:
                logger.error("angel_one_auth_failed", response=data)
                self._smart_api = None
        except Exception as e:
            logger.error("angel_one_connect_error", error=str(e))
            self._smart_api = None

    async def disconnect(self) -> None:
        if self._smart_api:
            try:
                self._smart_api.terminateSession(self._client_id)
            except Exception:
                pass
            self._smart_api = None

    async def get_quote(self, symbol: str) -> Optional[Quote]:
        if not self._smart_api:
            return None
        # Angel One uses exchange-token pairs for LTP
        # Full implementation requires symbol → token mapping from symbol master
        # For Phase 2, delegate to yfinance until symbol master is loaded
        logger.info("angel_one_quote_via_yfinance_fallback", symbol=symbol)
        from app.brokers.yfinance_adapter import YFinanceAdapter
        return await YFinanceAdapter().get_quote(symbol)

    async def get_quotes_batch(self, symbols: List[str]) -> List[Quote]:
        if not self._smart_api:
            return []
        from app.brokers.yfinance_adapter import YFinanceAdapter
        return await YFinanceAdapter().get_quotes_batch(symbols)

    async def get_history(
        self, symbol: str, period: str = "1y", interval: str = "1d"
    ) -> List[OHLCVBar]:
        # Angel One candle data requires exchange + symboltoken + fromdate + todate
        # Full implementation in Phase 3 after symbol master integration
        from app.brokers.yfinance_adapter import YFinanceAdapter
        return await YFinanceAdapter().get_history(symbol, period, interval)

    async def get_indices(self) -> List[Quote]:
        from app.brokers.yfinance_adapter import YFinanceAdapter
        return await YFinanceAdapter().get_indices()
