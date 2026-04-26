# NSE India — Primary Data Source Design

## The New Data Flow

### Live Prices (quotes, indices, screener)
```
REQUEST
  │
  ▼
Redis Cache (30s TTL)
  ├── HIT  → return instantly (zero external calls)
  └── MISS
        │
        ▼
   NSE India API  ← PRIMARY (1 call = all 50 symbols, ~15s delay, free)
        ├── SUCCESS → cache in Redis → return
        └── FAIL (maintenance / down)
              │
              ▼
         Broker API  ← FALLBACK (Angel One / Upstox, per user)
               └── SUCCESS / partial → cache in Redis → return
```

### Historical Data (charts, ML training, backfill)
```
REQUEST → Redis Cache (5 min TTL)
  └── MISS
        │
        ▼
   YFinance  ← ONLY source (free, years of NSE data, delay irrelevant for history)
        └── returns bars or empty list
```

---

## NEW FILE: `backend/app/brokers/nse_india_adapter.py`

```python
"""NSE India unofficial API adapter — primary live price source.

Calls the same JSON endpoints nseindia.com uses for its own pages.
No API key required. Session cookies obtained via curl_cffi (Chrome TLS).

Rate:      ~1 req/sec safe; background refresh calls once every 25s.
Data:      ~15-30s behind NEAT engine. Full OHLCV + volume.
Endpoints:
  /api/equity-stockIndices?index=NIFTY%2050  → all 50 Nifty stocks in 1 call
  /api/allIndices                            → all major indices
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

_NSE_INDICES = {
    "NIFTY 50":      "Nifty 50",
    "NIFTY BANK":    "Bank Nifty",
    "NIFTY IT":      "Nifty IT",
    "NIFTY PHARMA":  "Nifty Pharma",
    "NIFTY AUTO":    "Nifty Auto",
    "NIFTY NEXT 50": "Nifty Next 50",
}

# ── Circuit breaker ──────────────────────────────────────────────────────────
_CB_LOCK          = threading.Lock()
_CB_FAILURES      = 0
_CB_OPEN_UNTIL    = 0.0
_CB_THRESHOLD     = 5
_CB_RECOVERY_SECS = 120.0


def _is_circuit_open() -> bool:
    with _CB_LOCK:
        return bool(_CB_OPEN_UNTIL and time.monotonic() < _CB_OPEN_UNTIL)


def _record_success():
    global _CB_FAILURES, _CB_OPEN_UNTIL
    with _CB_LOCK:
        _CB_FAILURES = 0
        _CB_OPEN_UNTIL = 0.0


def _record_failure():
    global _CB_FAILURES, _CB_OPEN_UNTIL
    with _CB_LOCK:
        _CB_FAILURES += 1
        if _CB_FAILURES >= _CB_THRESHOLD:
            _CB_OPEN_UNTIL = time.monotonic() + _CB_RECOVERY_SECS
            logger.warning("nse_circuit_opened", failures=_CB_FAILURES)


def _safe_float(v) -> float:
    try:
        return round(float(str(v).replace(",", "")), 4)
    except Exception:
        return 0.0


def _make_quote(symbol: str, d: dict) -> Quote:
    price      = _safe_float(d.get("lastPrice") or d.get("last") or 0)
    prev_close = _safe_float(d.get("previousClose") or d.get("previousClosePrice") or price)
    return Quote(
        symbol=symbol,
        price=price,
        prev_close=prev_close,
        change=_safe_float(d.get("change") or (price - prev_close)),
        change_pct=_safe_float(d.get("pChange") or d.get("perChange") or 0),
        volume=int(_safe_float(d.get("totalTradedVolume") or 0)),
        high=_safe_float(d.get("dayHigh") or price),
        low=_safe_float(d.get("dayLow") or price),
        open=_safe_float(d.get("open") or price),
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


class NSEIndiaAdapter(BrokerAdapter):
    """Primary live price source — NSE India unofficial JSON API."""

    broker_name = "nse_india"

    def __init__(self):
        self._cookies: dict = {}
        self._session_ts: float = 0.0

    def is_credentials_configured(self) -> bool:
        return not _is_circuit_open()

    async def _ensure_session(self, session: AsyncSession) -> None:
        """Hit NSE homepage to acquire session cookies (nsit, nseappid)."""
        if time.monotonic() - self._session_ts < 300 and self._cookies:
            return
        try:
            r = await session.get(_BASE, headers=_HEADERS, timeout=_TIMEOUT)
            self._cookies = dict(r.cookies)
            self._session_ts = time.monotonic()
            logger.debug("nse_session_refreshed", keys=list(self._cookies.keys()))
        except Exception as exc:
            logger.warning("nse_session_failed", err=str(exc))

    async def _get_json(self, session: AsyncSession, path: str):
        url = f"{_BASE}{path}"
        try:
            r = await session.get(url, headers=_HEADERS, cookies=self._cookies, timeout=_TIMEOUT)
            if r.status_code in (401, 403):
                # Session expired — refresh once and retry
                self._cookies = {}
                self._session_ts = 0.0
                await self._ensure_session(session)
                r = await session.get(url, headers=_HEADERS, cookies=self._cookies, timeout=_TIMEOUT)
            r.raise_for_status()
            return r.json()
        except Exception as exc:
            logger.warning("nse_api_failed", path=path, err=str(exc))
            return None

    async def get_quotes_batch(self, symbols: List[str]) -> List[Quote]:
        """Fetch all symbols — one bulk API call covers all Nifty 50."""
        if _is_circuit_open():
            return []

        async with AsyncSession(impersonate="chrome124", timeout=_TIMEOUT) as session:
            await self._ensure_session(session)

            # One call covers all 50 Nifty stocks
            data = await self._get_json(session, "/api/equity-stockIndices?index=NIFTY%2050")
            if not data or "data" not in data:
                _record_failure()
                return []

            nse_map = {
                item["symbol"].upper(): item
                for item in data["data"]
                if "symbol" in item
            }

            quotes, missing = [], []
            for sym in symbols:
                clean = sym.upper().replace(".NS", "").replace(".BO", "")
                if clean in nse_map:
                    quotes.append(_make_quote(clean, nse_map[clean]))
                else:
                    missing.append(clean)

            # Individual calls for symbols not in Nifty 50
            for sym in missing:
                item = await self._get_json(session, f"/api/quote-equity?symbol={sym}")
                if item and "priceInfo" in item:
                    d = item["priceInfo"]
                    d["lastPrice"] = d.get("lastPrice") or item.get("lastPrice")
                    quotes.append(_make_quote(sym, d))
                await asyncio.sleep(0.2)  # polite spacing for individual calls

            _record_success()
            logger.info("nse_batch_fetched", total=len(quotes), individual=len(missing))
            return quotes

    async def get_quote(self, symbol: str) -> Optional[Quote]:
        if _is_circuit_open():
            return None
        clean = symbol.upper().replace(".NS", "").replace(".BO", "")
        async with AsyncSession(impersonate="chrome124", timeout=_TIMEOUT) as session:
            await self._ensure_session(session)
            data = await self._get_json(session, f"/api/quote-equity?symbol={clean}")
            if not data:
                _record_failure()
                return None
            d = data.get("priceInfo", {})
            d["lastPrice"] = d.get("lastPrice") or data.get("lastPrice")
            _record_success()
            return _make_quote(clean, d)

    async def get_indices(self) -> List[Quote]:
        if _is_circuit_open():
            return []
        async with AsyncSession(impersonate="chrome124", timeout=_TIMEOUT) as session:
            await self._ensure_session(session)
            data = await self._get_json(session, "/api/allIndices")
            if not data or "data" not in data:
                _record_failure()
                return []
            quotes = []
            for item in data["data"]:
                name = item.get("index", "")
                if name in _NSE_INDICES:
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
            return quotes

    async def get_history(
        self, symbol: str, period: str = "1y", interval: str = "1d"
    ) -> List[OHLCVBar]:
        """NSE historical not implemented — use YFinanceAdapter for backfill."""
        return []
```

---

## MODIFY: `backend/app/services/price_service.py`

Replace the current Google Finance fallback with the NSE-primary chain.

**New module-level setup (top of file):**
```python
from app.brokers.nse_india_adapter import NSEIndiaAdapter

_nse_adapter = NSEIndiaAdapter()   # PRIMARY — shared singleton

_QUOTE_TTL   = 30   # was 60 — NSE refreshes every ~15s so 30s is fine
_INDEX_TTL   = 30
_HISTORY_TTL = 300
```

**New `get_quotes_batch()`:**
```python
async def get_quotes_batch(adapter: BrokerAdapter, symbols: List[str]) -> List[Quote]:
    # Shared cache — all users share NSE data (no per-user keys needed)
    cache_key = f"nse:quotes:{','.join(sorted(s.upper() for s in symbols))}"
    redis = _get_redis_safe()
    if redis:
        cached = await redis.get(cache_key)
        if cached:
            return [Quote(**q) for q in json.loads(cached)]

    # 1. NSE India PRIMARY — 1 API call covers all 50 Nifty symbols
    quotes = await _nse_adapter.get_quotes_batch(symbols)

    # 2. Broker FALLBACK — only for symbols NSE missed
    fetched = {q.symbol for q in quotes}
    missing = [s for s in symbols if s.upper().replace(".NS","") not in fetched]
    if missing:
        try:
            broker_quotes = await adapter.get_quotes_batch(missing)
            quotes.extend(broker_quotes)
            logger.info("broker_filled_missing", count=len(broker_quotes))
        except Exception as exc:
            logger.warning("broker_fallback_failed", err=str(exc))

    if quotes and redis:
        await redis.setex(cache_key, _QUOTE_TTL, json.dumps([q.__dict__ for q in quotes]))
    return quotes
```

**New `get_indices()`:**
```python
async def get_indices(adapter: BrokerAdapter) -> List[Quote]:
    cache_key = "nse:indices"
    redis = _get_redis_safe()
    if redis:
        cached = await redis.get(cache_key)
        if cached:
            return [Quote(**q) for q in json.loads(cached)]

    quotes = await _nse_adapter.get_indices()      # 1. NSE primary
    if not quotes:
        quotes = await adapter.get_indices()       # 2. Broker fallback

    if quotes and redis:
        await redis.setex(cache_key, _INDEX_TTL, json.dumps([q.__dict__ for q in quotes]))
    return quotes
```

**New `get_history()` — YFinance only:**
```python
async def get_history(adapter, symbol, period="1y", interval="1d") -> List[OHLCVBar]:
    """History: YFinance ONLY. NSE and broker APIs not used for history."""
    cache_key = f"history:{symbol}:{period}:{interval}"
    redis = _get_redis_safe()
    if redis:
        cached = await redis.get(cache_key)
        if cached:
            return [OHLCVBar(**b) for b in json.loads(cached)]

    # YFinance is the sole source for historical data
    from app.brokers.yfinance_adapter import YFinanceAdapter
    bars = await YFinanceAdapter().get_history(symbol, period, interval)
    if bars:
        logger.info("yfinance_history_fetched", symbol=symbol, bars=len(bars))

    if bars and redis:
        await redis.setex(cache_key, _HISTORY_TTL, json.dumps([b.__dict__ for b in bars]))
    return bars
```

---

## MODIFY: `backend/app/brokers/factory.py`

Remove `ValueError` when no broker configured. NSE handles live prices regardless.

```python
async def get_adapter_for_user(user_id, preferred_broker, db) -> BrokerAdapter:
    """Returns adapter for ORDER EXECUTION.
    Live prices are sourced from NSEIndiaAdapter in price_service, not here.
    """
    if not preferred_broker:
        from app.brokers.yfinance_adapter import YFinanceAdapter
        logger.info("no_broker_configured_nse_handles_prices", user_id=user_id)
        return YFinanceAdapter()  # live methods return [] so NSE stays primary

    # ... rest of Angel One / Upstox logic unchanged ...
```

---

## MODIFY: `backend/app/services/screener_service.py`

One change in `get_screener_page()` — route through `price_service` instead of calling adapter directly.

```python
# Replace lines 108-119:
if not quote_map:
    from app.services import price_service
    try:
        quotes = await price_service.get_quotes_batch(adapter, symbols)
        quote_map = {q.symbol: q.__dict__ for q in quotes}
    except Exception as e:
        logger.error("screener_quotes_failed", err=str(e))
    if quote_map and redis:
        await redis.setex(cache_key, _SCREENER_CACHE_TTL, json.dumps(quote_map))
```

---

## MODIFY: `backend/app/brokers/yfinance_adapter.py`

Disable all live price methods. Keep `get_history` untouched.

```python
class YFinanceAdapter(BrokerAdapter):
    """Historical data backfill ONLY.
    Live methods disabled — NSEIndiaAdapter handles all live prices.
    """
    broker_name = "yfinance"

    async def get_quote(self, symbol: str) -> Optional[Quote]:
        return None  # Disabled — use NSEIndiaAdapter

    async def get_quotes_batch(self, symbols: List[str]) -> List[Quote]:
        return []    # Disabled — use NSEIndiaAdapter

    async def get_indices(self) -> List[Quote]:
        return []    # Disabled — use NSEIndiaAdapter

    async def get_history(self, symbol, period="1y", interval="1d") -> List[OHLCVBar]:
        # Unchanged — yf.download() still works for historical backfill
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._sync_history, symbol, period, interval)

    # _sync_history implementation below — no changes needed
```

---

## Redis Cache Key Changes

| Old Key | New Key | TTL | Notes |
|---------|---------|-----|-------|
| `shared:quote:<SYM>` | `nse:quote:<SYM>` | 30s | Shared, all users |
| `quote:angel_one:<SYM>` | *(deprecated)* | — | No longer needed |
| `indices:angel_one` | `nse:indices` | 30s | Shared, all users |
| `history:<broker>:<sym>:...` | `history:<sym>:...` | 5min | Broker-agnostic |

---

## Adapter Responsibility Matrix

| Adapter | Live Quotes | Indices | History | Orders |
|---------|-------------|---------|---------|--------|
| `NSEIndiaAdapter` | ✅ **PRIMARY** | ✅ **PRIMARY** | ❌ | ❌ |
| `YFinanceAdapter` | ❌ Disabled | ❌ Disabled | ✅ **ONLY SOURCE** | ❌ |
| `AngelOneAdapter` | ✅ Fallback | ✅ Fallback | ❌ | ✅ |
| `UpstoxAdapter` | ✅ Fallback | ✅ Fallback | ❌ | ✅ |

---

## Files to Change

| File | Action |
|------|--------|
| `brokers/nse_india_adapter.py` | **CREATE** — full NSE adapter |
| `services/price_service.py` | **MODIFY** — NSE primary, broker fallback, YF for history |
| `brokers/factory.py` | **MODIFY** — return YFinanceAdapter instead of ValueError |
| `services/screener_service.py` | **MODIFY** — route through price_service |
| `brokers/yfinance_adapter.py` | **MODIFY** — disable live methods, keep get_history |
| `brokers/google_finance_adapter.py` | **DORMANT** — file kept, not used by any code path |
| `brokers/angel_one.py` | **NO CHANGE** — used for orders + live price fallback |
| `brokers/upstox.py` | **NO CHANGE** — used for orders + live price fallback |
