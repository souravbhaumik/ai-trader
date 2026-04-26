"""NSE India unofficial API adapter — primary live price source.

Calls the same JSON endpoints that nseindia.com uses for its own pages.
No API key required. Session cookies (nsit, nseappid) are obtained by
hitting the NSE homepage via curl_cffi, which impersonates Chrome's TLS
fingerprint so Akamai's bot management accepts the request.

Data characteristics:
  - Live prices: ~15-30s behind NSE NEAT matching engine
  - Full OHLCV: open, high, low, last price, previous close, volume
  - Coverage:   all Nifty 50 in a single bulk call; non-Nifty via individual calls
  - Indices:    Nifty 50, Bank Nifty, IT, Pharma, Auto, Next 50

Rate:
  One bulk call every 30s (Redis TTL) serves all concurrent users.
  Individual calls for non-Nifty symbols: polite 200ms spacing.

Endpoints:
  /api/equity-stockIndices?index=NIFTY%2050  → all Nifty 50 stocks in 1 call
  /api/allIndices                            → all major index quotes
  /api/quote-equity?symbol=RELIANCE         → single stock deep quote
"""
from __future__ import annotations

import asyncio
import time
import threading
from datetime import datetime, timezone
from typing import List, Optional

import structlog
from curl_cffi.requests import AsyncSession

from app.brokers.base import BrokerAdapter, OHLCVBar, Quote

logger = structlog.get_logger(__name__)

_TIMEOUT = 12
_BASE    = "https://www.nseindia.com"
_SESSION_TTL = 300  # refresh NSE session cookies every 5 minutes

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":           "application/json, text/plain, */*",
    "Accept-Language":  "en-US,en;q=0.9",
    "Accept-Encoding":  "gzip, deflate, br",
    "Referer":          "https://www.nseindia.com/",
    "X-Requested-With": "XMLHttpRequest",
}

# Display names for major indices returned by /api/allIndices
_NSE_INDICES = {
    "NIFTY 50":      "Nifty 50",
    "NIFTY BANK":    "Bank Nifty",
    "NIFTY IT":      "Nifty IT",
    "NIFTY PHARMA":  "Nifty Pharma",
    "NIFTY AUTO":    "Nifty Auto",
    "NIFTY NEXT 50": "Nifty Next 50",
}

# ── Circuit breaker ───────────────────────────────────────────────────────────
_CB_LOCK          = threading.Lock()
_CB_FAILURES      = 0
_CB_OPEN_UNTIL    = 0.0
_CB_THRESHOLD     = 5
_CB_RECOVERY_SECS = 120.0


def _is_circuit_open() -> bool:
    with _CB_LOCK:
        return bool(_CB_OPEN_UNTIL and time.monotonic() < _CB_OPEN_UNTIL)


def _record_success() -> None:
    global _CB_FAILURES, _CB_OPEN_UNTIL
    with _CB_LOCK:
        _CB_FAILURES   = 0
        _CB_OPEN_UNTIL = 0.0


def _record_failure() -> None:
    global _CB_FAILURES, _CB_OPEN_UNTIL
    with _CB_LOCK:
        _CB_FAILURES += 1
        if _CB_FAILURES >= _CB_THRESHOLD:
            _CB_OPEN_UNTIL = time.monotonic() + _CB_RECOVERY_SECS
            logger.warning(
                "nse_india_circuit_opened",
                failures=_CB_FAILURES,
                recovery_secs=_CB_RECOVERY_SECS,
            )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_float(v) -> float:
    try:
        return round(float(str(v).replace(",", "")), 4)
    except Exception:
        return 0.0


def _clean_symbol(symbol: str) -> str:
    return symbol.upper().replace(".NS", "").replace(".BO", "").strip()


def _make_quote(symbol: str, d: dict) -> Quote:
    """Build a Quote from an NSE API response dict."""
    price      = _safe_float(d.get("lastPrice") or d.get("last") or 0)
    prev_close = _safe_float(
        d.get("previousClose") or d.get("previousClosePrice") or price
    )
    change     = _safe_float(d.get("change") or (price - prev_close))
    change_pct = _safe_float(d.get("pChange") or d.get("perChange") or 0)
    volume     = int(_safe_float(d.get("totalTradedVolume") or d.get("tradedVolume") or 0))
    return Quote(
        symbol=symbol,
        price=price,
        prev_close=prev_close,
        change=change,
        change_pct=change_pct,
        volume=volume,
        high=_safe_float(d.get("dayHigh") or price),
        low=_safe_float(d.get("dayLow") or price),
        open=_safe_float(d.get("open") or price),
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


# ── Adapter ───────────────────────────────────────────────────────────────────

class NSEIndiaAdapter(BrokerAdapter):
    """Primary live price source — NSE India unofficial JSON API.

    Uses curl_cffi to impersonate Chrome's full TLS fingerprint, which is
    required to pass Akamai's bot management on nseindia.com.

    Session management:
      Cookies (nsit, nseappid, ak_bmsc) are obtained by hitting the NSE
      homepage and are reused for up to 5 minutes before a silent refresh.
    """

    broker_name = "nse_india"

    # Class-level session state — shared across all instances so only one
    # homepage hit occurs every _SESSION_TTL seconds regardless of how many
    # adapter instances exist (price_service singleton, ws.py, tests, etc.)
    _cookies:    dict  = {}
    _session_ts: float = 0.0
    _session_lock = threading.Lock()

    def __init__(self) -> None:
        pass  # session state is class-level

    def is_credentials_configured(self) -> bool:
        """NSE needs no credentials — only blocked when circuit is open."""
        return not _is_circuit_open()

    # ── Session ───────────────────────────────────────────────────────────────

    async def _ensure_session(self, session: AsyncSession) -> None:
        """Warm up NSE session cookies by hitting the homepage if stale."""
        if time.monotonic() - NSEIndiaAdapter._session_ts < _SESSION_TTL and NSEIndiaAdapter._cookies:
            return
        try:
            r = await session.get(_BASE, headers=_HEADERS, timeout=_TIMEOUT)
            NSEIndiaAdapter._cookies    = dict(r.cookies)
            NSEIndiaAdapter._session_ts = time.monotonic()
            logger.debug("nse_session_refreshed", cookie_keys=list(NSEIndiaAdapter._cookies.keys()))
        except Exception as exc:
            logger.warning("nse_session_refresh_failed", err=str(exc))

    async def _get_json(self, session: AsyncSession, path: str):
        """GET an NSE API endpoint, auto-refreshing the session on 401/403."""
        url = f"{_BASE}{path}"
        try:
            r = await session.get(
                url, headers=_HEADERS, cookies=NSEIndiaAdapter._cookies, timeout=_TIMEOUT
            )
            if r.status_code in (401, 403):
                # Session cookie expired — refresh once and retry
                NSEIndiaAdapter._cookies    = {}
                NSEIndiaAdapter._session_ts = 0.0
                await self._ensure_session(session)
                r = await session.get(
                    url, headers=_HEADERS, cookies=NSEIndiaAdapter._cookies, timeout=_TIMEOUT
                )
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            logger.warning("nse_api_request_failed", path=path, err=str(exc))
            return None

    # ── BrokerAdapter interface ───────────────────────────────────────────────

    async def get_quotes_batch(self, symbols: List[str]) -> List[Quote]:
        """Fetch live quotes — one bulk call covers all 50 Nifty stocks.

        Non-Nifty symbols fall through to individual /api/quote-equity calls
        with a 200ms pause between them to stay within NSE's rate tolerance.
        """
        if _is_circuit_open():
            logger.info("nse_circuit_open_skipping_batch")
            return []

        async with AsyncSession(impersonate="chrome124", timeout=_TIMEOUT) as session:
            await self._ensure_session(session)

            # One call → all 50 Nifty stocks
            data = await self._get_json(
                session, "/api/equity-stockIndices?index=NIFTY%2050"
            )
            if not data or "data" not in data:
                _record_failure()
                logger.warning("nse_bulk_fetch_failed")
                return []

            # Build a lookup map from the bulk response
            nse_map: dict[str, dict] = {
                item["symbol"].upper(): item
                for item in data["data"]
                if "symbol" in item
            }

            quotes: List[Quote] = []
            missing: List[str]  = []

            for sym in symbols:
                clean = _clean_symbol(sym)
                if clean in nse_map:
                    quotes.append(_make_quote(clean, nse_map[clean]))
                else:
                    missing.append(clean)

            # Individual calls for symbols not in the Nifty 50 bulk response
            for sym in missing:
                item = await self._get_json(session, f"/api/quote-equity?symbol={sym}")
                if item and "priceInfo" in item:
                    d = item["priceInfo"]
                    # lastPrice lives at priceInfo level
                    d.setdefault("lastPrice", item.get("lastPrice"))
                    quotes.append(_make_quote(sym, d))
                else:
                    logger.debug("nse_symbol_not_found", symbol=sym)
                await asyncio.sleep(0.2)  # polite spacing for individual calls

            _record_success()
            logger.info(
                "nse_batch_complete",
                total=len(quotes),
                bulk=len(quotes) - len([s for s in missing if s in {q.symbol for q in quotes}]),
                individual=len(missing),
            )
            return quotes

    async def get_quote(self, symbol: str) -> Optional[Quote]:
        """Fetch a single live quote via /api/quote-equity."""
        if _is_circuit_open():
            return None
        clean = _clean_symbol(symbol)
        async with AsyncSession(impersonate="chrome124", timeout=_TIMEOUT) as session:
            await self._ensure_session(session)
            data = await self._get_json(session, f"/api/quote-equity?symbol={clean}")
            if not data:
                _record_failure()
                return None
            d = data.get("priceInfo", {})
            d.setdefault("lastPrice", data.get("lastPrice"))
            _record_success()
            return _make_quote(clean, d)

    async def get_indices(self) -> List[Quote]:
        """Fetch live quotes for major NSE indices via /api/allIndices."""
        if _is_circuit_open():
            return []
        async with AsyncSession(impersonate="chrome124", timeout=_TIMEOUT) as session:
            await self._ensure_session(session)
            data = await self._get_json(session, "/api/allIndices")
            if not data or "data" not in data:
                _record_failure()
                return []

            quotes: List[Quote] = []
            for item in data["data"]:
                name = item.get("index", "")
                if name not in _NSE_INDICES:
                    continue
                q = _make_quote(_NSE_INDICES[name], {
                    "lastPrice":         item.get("last"),
                    "previousClose":     item.get("previousClose"),
                    "change":            item.get("change"),
                    "pChange":           item.get("percentChange"),
                    "dayHigh":           item.get("high"),
                    "dayLow":            item.get("low"),
                    "open":              item.get("open"),
                    "totalTradedVolume": 0,
                })
                q.symbol = _NSE_INDICES[name]
                quotes.append(q)

            _record_success()
            logger.info("nse_indices_fetched", count=len(quotes))
            return quotes

    async def get_history(
        self, symbol: str, period: str = "1y", interval: str = "1d"
    ) -> List[OHLCVBar]:
        """Historical data not available via NSE unofficial API.

        Use YFinanceAdapter.get_history() for all historical backfill.
        """
        return []
