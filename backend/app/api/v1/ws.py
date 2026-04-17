"""WebSocket endpoint for live price streaming — Phase 2.

URL: ``ws://<host>/api/v1/ws/prices?token=<jwt>&symbols=RELIANCE.NS,TCS.NS``

Architecture
------------
* A **single** background asyncio task (``price_broadcaster``) runs for the
  lifetime of the FastAPI process. Every ``PUSH_INTERVAL_SECS`` seconds it
  collects all unique symbols across every active connection, fetches prices
  from yfinance **once**, and fans the results into each connection's queue.
* Each WebSocket handler registers with the ``ConnectionManager``, reads from
  its private ``asyncio.Queue``, and calls ``disconnect`` on exit.

This ensures we never hit Yahoo Finance more than once per interval regardless
of how many concurrent clients are connected.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Dict, Set, Tuple

import structlog
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from jose import JWTError

from app.core.security import decode_access_token

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["websocket"])

_PUSH_INTERVAL_SECS = 15
_MAX_SYMBOLS        = 20


# ══════════════════════════════════════════════════════════════════════════════
#  Connection Manager
# ══════════════════════════════════════════════════════════════════════════════

class ConnectionManager:
    """Fan-out hub: one queue per active WebSocket client."""

    def __init__(self) -> None:
        # ws_id → (requested_symbols, output_queue)
        self._connections: Dict[int, Tuple[Set[str], asyncio.Queue]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, ws_id: int, symbols: Set[str]) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=8)
        async with self._lock:
            self._connections[ws_id] = (symbols, q)
        return q

    async def disconnect(self, ws_id: int) -> None:
        async with self._lock:
            self._connections.pop(ws_id, None)

    def all_symbols(self) -> Set[str]:
        """Union of every symbol currently being watched."""
        result: Set[str] = set()
        for syms, _ in self._connections.values():
            result |= syms
        return result

    async def broadcast(self, prices: list) -> None:
        """Route relevant prices into each connection's queue."""
        prices_by_sym = {p["symbol"]: p for p in prices}
        async with self._lock:
            snapshot = list(self._connections.values())
        for syms, queue in snapshot:
            relevant = [prices_by_sym[s] for s in syms if s in prices_by_sym]
            if relevant:
                try:
                    queue.put_nowait({"type": "prices", "data": relevant})
                except asyncio.QueueFull:
                    pass  # slow client — silently drop stale frame


# Module-level singleton — shared across all WebSocket connections.
manager = ConnectionManager()


# ══════════════════════════════════════════════════════════════════════════════
#  Single shared price fetcher (runs in a thread pool)
# ══════════════════════════════════════════════════════════════════════════════

def _fetch_prices_sync(symbols: list) -> list:
    """Fetch the latest price for each symbol via yfinance (sync, thread-safe)."""
    try:
        import yfinance as yf  # type: ignore
    except ImportError:
        return []

    result = []
    for sym in symbols:
        try:
            fi      = yf.Ticker(sym).fast_info
            price   = getattr(fi, "last_price", None)
            prev_cl = getattr(fi, "previous_close", None)
            if price is None:
                continue
            change_pct = (
                round((price - prev_cl) / prev_cl * 100, 2)
                if prev_cl and prev_cl != 0 else 0.0
            )
            result.append({
                "symbol":     sym,
                "price":      round(float(price), 2),
                "change_pct": change_pct,
                "ts":         int(time.time()),
            })
        except Exception as exc:  # noqa: BLE001
            logger.debug("ws.fetch_skip", symbol=sym, error=str(exc))
    return result


# ══════════════════════════════════════════════════════════════════════════════
#  Background broadcaster (started once in FastAPI lifespan)
# ══════════════════════════════════════════════════════════════════════════════

async def price_broadcaster() -> None:
    """Infinite loop: fetch prices for all subscribed symbols, fan out to queues.

    Called once from ``app.main.lifespan``; runs for the process lifetime.
    Sleeps first so the first fetch happens after clients have connected.
    """
    loop = asyncio.get_event_loop()
    while True:
        await asyncio.sleep(_PUSH_INTERVAL_SECS)
        try:
            symbols = list(manager.all_symbols())
            if not symbols:
                continue
            prices = await loop.run_in_executor(None, _fetch_prices_sync, symbols)
            if prices:
                await manager.broadcast(prices)
        except Exception as exc:  # noqa: BLE001
            logger.warning("price_broadcaster.error", error=str(exc))


# ══════════════════════════════════════════════════════════════════════════════
#  WebSocket endpoint
# ══════════════════════════════════════════════════════════════════════════════

@router.websocket("/ws/prices")
async def ws_prices(
    websocket: WebSocket,
    token: str = Query(..., description="JWT access token"),
    symbols: str = Query("", description="Comma-separated ticker symbols"),
):
    """Stream live price updates via a fan-out queue (not per-client polling)."""
    # ── Auth ─────────────────────────────────────────────────────────────────
    try:
        payload = decode_access_token(token)
    except JWTError:
        await websocket.close(code=4001, reason="Invalid or expired token.")
        return

    # ── Blocklist check ───────────────────────────────────────────────────────
    jti = payload.get("jti")
    if jti:
        try:
            from app.core.redis_client import get_redis
            if await get_redis().exists(f"blocklist:{jti}"):
                await websocket.close(code=4001, reason="Token has been revoked.")
                return
        except Exception:
            pass  # Redis unavailable — allow connection, not a hard dependency

    user_id = payload.get("sub", "unknown")

    # ── Parse symbols ─────────────────────────────────────────────────────────
    requested: Set[str] = {
        s.strip().upper()
        for s in symbols.split(",")
        if s.strip()
    }
    if not requested:
        await websocket.close(code=4002, reason="No symbols requested.")
        return
    if len(requested) > _MAX_SYMBOLS:
        requested = set(list(requested)[:_MAX_SYMBOLS])

    await websocket.accept()
    ws_id = id(websocket)
    queue = await manager.connect(ws_id, requested)
    logger.info("ws.prices.connected", user_id=user_id, symbols=list(requested))

    await websocket.send_text(json.dumps({
        "type":          "connected",
        "symbols":       list(requested),
        "interval_secs": _PUSH_INTERVAL_SECS,
    }))

    try:
        while True:
            # Block until the broadcaster puts something in this client's queue.
            # We use a timeout so a dead broadcaster doesn't strand the client.
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=60)
            except asyncio.TimeoutError:
                # Send a keepalive ping to detect stale connections
                await websocket.send_text(json.dumps({"type": "ping"}))
                continue

            await websocket.send_text(json.dumps(msg))

    except WebSocketDisconnect:
        logger.info("ws.prices.disconnected", user_id=user_id)
    except Exception as exc:
        logger.error("ws.prices.error", user_id=user_id, error=str(exc))
        try:
            await websocket.close(code=1011)
        except Exception:
            pass
    finally:
        await manager.disconnect(ws_id)

