"""WebSocket endpoints for live price and signal streaming.

Price URL:  ``ws://<host>/api/v1/ws/prices?token=<jwt>&symbols=RELIANCE.NS,TCS.NS``
Signal URL: ``ws://<host>/api/v1/ws/signals?token=<jwt>``

Architecture (prices)
---------------------
* A **single** background asyncio task (``price_broadcaster``) runs for the
  lifetime of the FastAPI process. Every ``PUSH_INTERVAL_SECS`` seconds it
  collects all unique symbols across every active connection and fans price
  updates to each connection's queue.

  Live price fetching is disabled until the shared credential pool is
  implemented (DESIGN.md §22). The broadcaster sends no price frames until
  then; clients retain their last-known values.

Architecture (signals)
----------------------
* A **single** background asyncio task (``signal_broadcaster``) subscribes to
  the Redis pub/sub channel ``signals:new``.  Whenever the Celery
  signal_generator task inserts a new signal it publishes the payload there.
  The broadcaster fans the JSON payload into every connected signal client's
  queue, so the frontend receives new signals in real-time without polling.
"""
from __future__ import annotations

import asyncio
import json
from typing import Dict, List, Set, Tuple

import structlog
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from jose import JWTError

from app.core.security import decode_access_token

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["websocket"])

_PUSH_INTERVAL_SECS = 15
_MAX_SYMBOLS        = 20
_SIGNALS_CHANNEL    = "signals:new"


# ══════════════════════════════════════════════════════════════════════════════
#  Connection Manager
# ══════════════════════════════════════════════════════════════════════════════

class ConnectionManager:
    """Fan-out hub: one queue per active WebSocket client."""

    def __init__(self) -> None:
        # ws_id → (user_id_str, requested_symbols, output_queue)
        self._connections: Dict[int, Tuple[str, Set[str], asyncio.Queue]] = {}
        # user_id_str → {ws_id, ...}  (one user may have multiple tabs)
        self._user_index: Dict[str, Set[int]] = {}
        self._lock = asyncio.Lock()

    async def connect(self, ws_id: int, user_id: str, symbols: Set[str]) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=8)
        async with self._lock:
            self._connections[ws_id] = (user_id, symbols, q)
            self._user_index.setdefault(user_id, set()).add(ws_id)
        return q

    async def disconnect(self, ws_id: int) -> None:
        async with self._lock:
            entry = self._connections.pop(ws_id, None)
            if entry:
                uid = entry[0]
                self._user_index.get(uid, set()).discard(ws_id)
                if not self._user_index.get(uid):
                    self._user_index.pop(uid, None)

    def all_symbols(self) -> Set[str]:
        """Union of every symbol currently being watched."""
        result: Set[str] = set()
        for _, syms, _ in self._connections.values():
            result |= syms
        return result

    async def broadcast(self, prices: list) -> None:
        """Route relevant prices into each connection's queue."""
        prices_by_sym = {p["symbol"]: p for p in prices}
        async with self._lock:
            snapshot = [(syms, q) for _, syms, q in self._connections.values()]
        for syms, queue in snapshot:
            relevant = [prices_by_sym[s] for s in syms if s in prices_by_sym]
            if relevant:
                try:
                    queue.put_nowait({"type": "prices", "data": relevant})
                except asyncio.QueueFull:
                    pass  # slow client — silently drop stale frame

    async def send_to_user(self, user_id: str, message: dict) -> None:
        """Push a message to all active WS connections for a specific user."""
        async with self._lock:
            ws_ids = list(self._user_index.get(user_id, set()))
            queues = [
                self._connections[wid][2]
                for wid in ws_ids
                if wid in self._connections
            ]
        for q in queues:
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                pass


# Module-level singleton — shared across all WebSocket connections.
manager = ConnectionManager()


# ══════════════════════════════════════════════════════════════════════════════
#  Single shared price fetcher (runs in a thread pool)
# ══════════════════════════════════════════════════════════════════════════════

def _fetch_prices_sync(symbols: list) -> list:
    """Fetch live prices via the shared broker credential pool."""
    try:
        from app.brokers.credential_pool import get_quotes_batch_via_pool

        normalized = [str(s).upper().strip() for s in symbols if str(s).strip()]
        if not normalized:
            return []
        return get_quotes_batch_via_pool(normalized)
    except Exception as exc:
        logger.warning("ws.pool_price_fetch_failed", err=str(exc))
        return []


# ══════════════════════════════════════════════════════════════════════════════
#  Background broadcaster (started once in FastAPI lifespan)
# ══════════════════════════════════════════════════════════════════════════════

async def price_broadcaster() -> None:
    """Infinite loop: fetch prices for all subscribed symbols, fan out to queues.

    Called once from ``app.main.lifespan``; runs for the process lifetime.
    Sleeps first so the first fetch happens after clients have connected.
    """
    loop = asyncio.get_running_loop()
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
            logger.warning("price_broadcaster.error", err=str(exc))


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
    queue = await manager.connect(ws_id, user_id, requested)
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
        logger.error("ws.prices.error", user_id=user_id, err=str(exc))
        try:
            await websocket.close(code=1011)
        except Exception:
            pass
    finally:
        await manager.disconnect(ws_id)


# ══════════════════════════════════════════════════════════════════════════════
#  Signal fan-out (Redis pub/sub → all connected /ws/signals clients)
# ══════════════════════════════════════════════════════════════════════════════

class SignalConnectionManager:
    """Simple fan-out: broadcast every new signal to all connected clients."""

    def __init__(self) -> None:
        self._queues: List[asyncio.Queue] = []
        self._lock = asyncio.Lock()

    async def connect(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=64)
        async with self._lock:
            self._queues.append(q)
        return q

    async def disconnect(self, q: asyncio.Queue) -> None:
        async with self._lock:
            try:
                self._queues.remove(q)
            except ValueError:
                pass

    async def broadcast(self, message: dict) -> None:
        async with self._lock:
            snapshot = list(self._queues)
        for q in snapshot:
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                pass  # slow client — drop stale signal frame


signal_manager = SignalConnectionManager()


async def signal_broadcaster() -> None:
    """Subscribe to Redis ``signals:new`` and fan out to all WS signal clients.

    The Celery signal_generator task publishes each new signal as JSON to this
    channel immediately after the DB INSERT.  This task forwards the message to
    every active ``/ws/signals`` connection so the frontend updates without polling.
    """
    import redis.asyncio as aioredis  # noqa: PLC0415
    from app.core.config import settings  # noqa: PLC0415

    while True:
        try:
            # Create a *dedicated* connection for pub/sub (cannot share the pool)
            pubsub_conn = aioredis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_timeout=None,  # block indefinitely waiting for messages
            )
            async with pubsub_conn.pubsub() as ps:
                await ps.subscribe(_SIGNALS_CHANNEL)
                logger.info("signal_broadcaster.subscribed", channel=_SIGNALS_CHANNEL)
                async for raw in ps.listen():
                    if raw["type"] != "message":
                        continue
                    try:
                        payload = json.loads(raw["data"])
                    except (json.JSONDecodeError, TypeError):
                        continue
                    await signal_manager.broadcast({"type": "signal", "data": payload})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("signal_broadcaster.error_reconnecting", err=str(exc))
            await asyncio.sleep(3)  # brief back-off before reconnecting


# ══════════════════════════════════════════════════════════════════════════════
#  WebSocket endpoint — /ws/signals
# ══════════════════════════════════════════════════════════════════════════════

@router.websocket("/ws/signals")
async def ws_signals(
    websocket: WebSocket,
    token: str = Query(..., description="JWT access token"),
):
    """Stream new AI signals in real-time.

    The client receives a ``{"type": "signal", "data": {...}}`` message every
    time the signal_generator Celery task inserts a new signal.  No symbols
    parameter needed — all symbols are broadcast to all subscribers.
    """
    # ── Auth ──────────────────────────────────────────────────────────────────
    try:
        payload = decode_access_token(token)
    except JWTError:
        await websocket.close(code=4001, reason="Invalid or expired token.")
        return

    jti = payload.get("jti")
    if jti:
        try:
            from app.core.redis_client import get_redis  # noqa: PLC0415
            if await get_redis().exists(f"blocklist:{jti}"):
                await websocket.close(code=4001, reason="Token has been revoked.")
                return
        except Exception:
            pass

    user_id = payload.get("sub", "unknown")

    await websocket.accept()
    queue = await signal_manager.connect()
    logger.info("ws.signals.connected", user_id=user_id)

    await websocket.send_text(json.dumps({"type": "connected", "channel": "signals"}))

    try:
        while True:
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=60)
            except asyncio.TimeoutError:
                await websocket.send_text(json.dumps({"type": "ping"}))
                continue
            await websocket.send_text(json.dumps(msg))
    except WebSocketDisconnect:
        logger.info("ws.signals.disconnected", user_id=user_id)
    except Exception as exc:
        logger.error("ws.signals.error", user_id=user_id, err=str(exc))
        try:
            await websocket.close(code=1011)
        except Exception:
            pass
    finally:
        await signal_manager.disconnect(queue)

