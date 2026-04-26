"""Google Finance web scraper — last-resort fallback for live prices.

Scrapes https://www.google.com/finance/quote/SYMBOL:NSE when primary broker
adapters (Upstox / Angel One) are unavailable (e.g. expired OAuth token).
No API key required. Use conservatively — not for bulk/fundamentals fetching.

Limitations:
  - Only returns live price (open/high/low/volume are 0)
  - Concurrent batch requests are capped via semaphore to avoid blocks
  - Circuit breaker opens after 5 failures, recovers after 2 min
"""
from __future__ import annotations

import asyncio
import re
import threading
import time
from datetime import datetime, timezone
from typing import List, Optional

import httpx
import structlog

from app.brokers.base import BrokerAdapter, OHLCVBar, Quote

logger = structlog.get_logger(__name__)

_TIMEOUT        = 10
_BATCH_SEM      = asyncio.Semaphore(5)   # max 5 concurrent GF requests

# ── Circuit breaker ───────────────────────────────────────────────────────────
_CB_LOCK          = threading.Lock()
_CB_FAILURES      = 0
_CB_OPEN_UNTIL: float = 0.0
_CB_THRESHOLD     = 5
_CB_RECOVERY_SECS = 120.0   # 2 min — be conservative with scraping

# ── Index → Google Finance instrument key ─────────────────────────────────────
_GF_INDICES = {
    ".NSEI:INDEXNSE":  "Nifty 50",
    ".BSESN:INDEXBOM": "Sensex",
    ".NSEBANK:INDEXNSE": "Bank Nifty",
    ".CNXIT:INDEXNSE": "Nifty IT",
}

# Browser-like headers so Google doesn't immediately reject the request
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

# Price extraction patterns — tried in order; first match wins
_PRICE_RE = [
    re.compile(r'data-last-price="([\d.]+)"'),
    re.compile(r'"price":\s*([\d.]+)'),
    re.compile(r'class="YMlKec[^"]*"[^>]*>([\d,]+\.?\d*)'),
]

# Change % extraction (nice-to-have; falls back to 0.0)
_CHANGE_PCT_RE = re.compile(r'data-percent-change="(-?[\d.]+)"')


# ── Circuit breaker helpers ───────────────────────────────────────────────────

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
                "google_finance_circuit_opened",
                failures=_CB_FAILURES,
                recovery_secs=_CB_RECOVERY_SECS,
            )


# ── URL helpers ───────────────────────────────────────────────────────────────

def _symbol_to_gf_key(symbol: str) -> str:
    """Convert RELIANCE or RELIANCE.NS → RELIANCE:NSE."""
    clean = symbol.upper().replace(".NS", "").replace(".BO", "").strip()
    return f"{clean}:NSE"


def _parse_price(html: str) -> Optional[float]:
    for pat in _PRICE_RE:
        m = pat.search(html)
        if m:
            try:
                return float(m.group(1).replace(",", ""))
            except ValueError:
                continue
    return None


def _parse_change_pct(html: str) -> float:
    m = _CHANGE_PCT_RE.search(html)
    if m:
        try:
            return float(m.group(1))
        except ValueError:
            pass
    return 0.0


def _make_quote(symbol: str, price: float, change_pct: float) -> Quote:
    return Quote(
        symbol=symbol.upper().replace(".NS", "").replace(".BO", ""),
        price=price,
        prev_close=round(price / (1 + change_pct / 100), 2) if change_pct else price,
        change=round(price * change_pct / 100, 2) if change_pct else 0.0,
        change_pct=change_pct,
        volume=0,
        high=0.0,
        low=0.0,
        open=0.0,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )


# ── Adapter class ─────────────────────────────────────────────────────────────

class GoogleFinanceAdapter(BrokerAdapter):
    """Scrapes Google Finance for live equity prices. Use as fallback only."""

    broker_name = "google_finance"

    def is_credentials_configured(self) -> bool:
        return not _is_circuit_open()

    async def _fetch_one(
        self,
        client: httpx.AsyncClient,
        gf_key: str,
        original_symbol: str,
    ) -> Optional[Quote]:
        url = f"https://www.google.com/finance/quote/{gf_key}"
        try:
            async with _BATCH_SEM:
                r = await client.get(url)
            r.raise_for_status()
            price = _parse_price(r.text)
            if price is None:
                _record_failure()
                logger.warning("google_finance_parse_failed", symbol=original_symbol, url=url)
                return None
            change_pct = _parse_change_pct(r.text)
            _record_success()
            return _make_quote(original_symbol, price, change_pct)
        except Exception as exc:
            _record_failure()
            logger.warning("google_finance_fetch_failed", symbol=original_symbol, err=str(exc))
            return None

    async def get_quote(self, symbol: str) -> Optional[Quote]:
        if _is_circuit_open():
            return None
        async with httpx.AsyncClient(
            timeout=_TIMEOUT, headers=_HEADERS, follow_redirects=True
        ) as client:
            return await self._fetch_one(client, _symbol_to_gf_key(symbol), symbol)

    async def get_quotes_batch(self, symbols: List[str]) -> List[Quote]:
        if _is_circuit_open():
            return []
        async with httpx.AsyncClient(
            timeout=_TIMEOUT, headers=_HEADERS, follow_redirects=True
        ) as client:
            tasks = [
                self._fetch_one(client, _symbol_to_gf_key(sym), sym)
                for sym in symbols
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)
        return [r for r in results if isinstance(r, Quote)]

    async def get_indices(self) -> List[Quote]:
        if _is_circuit_open():
            return []
        quotes: List[Quote] = []
        async with httpx.AsyncClient(
            timeout=_TIMEOUT, headers=_HEADERS, follow_redirects=True
        ) as client:
            for gf_key, display_name in _GF_INDICES.items():
                q = await self._fetch_one(client, gf_key, display_name)
                if q:
                    q.symbol = display_name  # keep human-readable name for indices
                    quotes.append(q)
        return quotes

    async def get_history(
        self, symbol: str, period: str = "1y", interval: str = "1d"
    ) -> List[OHLCVBar]:
        return []  # historical data not available via scraping


# ── Sync wrapper for WebSocket thread pool ────────────────────────────────────

def get_quotes_via_google_finance(symbols: list[str]) -> list[dict]:
    """Sync wrapper — safe to call from ``asyncio.run()`` in a thread pool."""
    adapter = GoogleFinanceAdapter()

    async def _run() -> list[dict]:
        quotes = await adapter.get_quotes_batch(symbols)
        return [q.__dict__ for q in quotes]

    return asyncio.run(_run())
