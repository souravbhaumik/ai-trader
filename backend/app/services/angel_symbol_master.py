"""Angel One instrument master — token lookup utility.

Downloads the public OpenAPIScripMaster.json from Angel One CDN on first use
and keeps the NSE/BSE equity instruments in an in-memory dict.

Usage (async):
    from app.services.angel_symbol_master import get_token
    result = await get_token("RELIANCE")
    # → {"exchange": "NSE", "tradingsymbol": "RELIANCE-EQ", "token": "2885"}
    # Returns None if symbol not found.

Usage (sync, Celery workers — only after master has been loaded):
    from app.services.angel_symbol_master import get_token_sync
    result = get_token_sync("RELIANCE")
"""
from __future__ import annotations

import asyncio
import json
import logging
import urllib.request
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# ── In-memory cache: normalized_symbol → (exchange, tradingsymbol, token) ────
_CACHE: Dict[str, Tuple[str, str, str]] = {}
_LOADED = False
_LOAD_LOCK = asyncio.Lock()

_MASTER_URL = (
    "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
)

# Hard-coded well-known index tokens (Angel One special tokens)
_INDEX_TOKENS: Dict[str, Tuple[str, str, str]] = {
    "NIFTY":      ("NSE", "Nifty 50",          "99926000"),
    "NIFTY50":    ("NSE", "Nifty 50",          "99926000"),
    "BANKNIFTY":  ("NSE", "Nifty Bank",        "99926009"),
    "NIFTYIT":    ("NSE", "Nifty IT",          "99926012"),
    "NIFTYMID50": ("NSE", "Nifty Midcap 50",   "99926014"),
    "SENSEX":     ("BSE", "SENSEX",            "1"),
}


# ── Helpers ────────────────────────────────────────────────────────────────────

def _normalize(symbol: str) -> str:
    """Strip exchange suffix / equity marker and normalize to uppercase."""
    s = symbol.upper().strip()
    for suffix in (".NS", ".BO", "-EQ", "-BE", "-N1", "-T1", "-IL"):
        if s.endswith(suffix):
            s = s[: -len(suffix)]
    return s


def _fetch_json(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "ai-trader/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        return json.loads(resp.read())


# ── Master load ────────────────────────────────────────────────────────────────

async def _load_master() -> None:
    global _LOADED  # noqa: PLW0603
    async with _LOAD_LOCK:
        if _LOADED:
            return
        try:
            logger.info("angel_symbol_master: downloading instrument master …")
            loop = asyncio.get_event_loop()
            raw: list = await loop.run_in_executor(None, _fetch_json, _MASTER_URL)
            count = 0
            for entry in raw:
                seg = entry.get("exch_seg", "")
                if seg not in ("NSE", "BSE"):
                    continue
                # Only equity instruments (no futures/options/currency)
                inst_type = entry.get("instrumenttype", "")
                if inst_type and inst_type not in ("", "EQ"):
                    continue
                sym = entry.get("symbol", "")
                tok = entry.get("token", "")
                if not sym or not tok:
                    continue
                norm = _normalize(sym)
                if norm not in _CACHE:          # first exchange wins (NSE preferred)
                    _CACHE[norm] = (seg, sym, tok)
                elif seg == "NSE" and _CACHE[norm][0] == "BSE":
                    _CACHE[norm] = (seg, sym, tok)  # prefer NSE over BSE
                count += 1
            logger.info("angel_symbol_master: loaded %d equity instruments", count)
            _LOADED = True
        except Exception as exc:  # noqa: BLE001
            logger.error("angel_symbol_master: load failed: %s", exc)


def ensure_loaded_sync() -> None:
    """Blocking load — for Celery workers that cannot await."""
    global _LOADED  # noqa: PLW0603
    if _LOADED:
        return
    try:
        logger.info("angel_symbol_master: downloading instrument master (sync) …")
        raw: list = _fetch_json(_MASTER_URL)
        count = 0
        for entry in raw:
            seg = entry.get("exch_seg", "")
            if seg not in ("NSE", "BSE"):
                continue
            inst_type = entry.get("instrumenttype", "")
            if inst_type and inst_type not in ("", "EQ"):
                continue
            sym = entry.get("symbol", "")
            tok = entry.get("token", "")
            if not sym or not tok:
                continue
            norm = _normalize(sym)
            if norm not in _CACHE:
                _CACHE[norm] = (seg, sym, tok)
            elif seg == "NSE" and _CACHE[norm][0] == "BSE":
                _CACHE[norm] = (seg, sym, tok)
            count += 1
        logger.info("angel_symbol_master: loaded %d instruments (sync)", count)
        _LOADED = True
    except Exception as exc:  # noqa: BLE001
        logger.error("angel_symbol_master: sync load failed: %s", exc)


# ── Public API ─────────────────────────────────────────────────────────────────

async def get_token(symbol: str) -> Optional[dict]:
    """Return {"exchange", "tradingsymbol", "token"} for a symbol, or None."""
    norm = _normalize(symbol)

    # Check index tokens first (no download needed)
    if norm in _INDEX_TOKENS:
        exc, ts, tok = _INDEX_TOKENS[norm]
        return {"exchange": exc, "tradingsymbol": ts, "token": tok}

    if not _LOADED:
        await _load_master()

    entry = _CACHE.get(norm)
    if entry:
        return {"exchange": entry[0], "tradingsymbol": entry[1], "token": entry[2]}

    logger.warning("angel_symbol_master: symbol not found: %s (normalized: %s)", symbol, norm)
    return None


def get_token_sync(symbol: str) -> Optional[dict]:
    """Synchronous version — only works after master has been loaded once."""
    norm = _normalize(symbol)
    if norm in _INDEX_TOKENS:
        exc, ts, tok = _INDEX_TOKENS[norm]
        return {"exchange": exc, "tradingsymbol": ts, "token": tok}
    if not _LOADED:
        ensure_loaded_sync()
    entry = _CACHE.get(norm)
    if entry:
        return {"exchange": entry[0], "tradingsymbol": entry[1], "token": entry[2]}
    return None


def preload_tokens(symbols: list[str]) -> None:
    """Pre-populate cache for a list of symbols (no-op if already loaded)."""
    if not _LOADED:
        ensure_loaded_sync()
