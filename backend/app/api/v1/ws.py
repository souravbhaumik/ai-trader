"""WebSocket endpoint for live price streaming — Phase 2.

URL: ``ws://<host>/api/v1/ws/prices?token=<jwt>&symbols=RELIANCE.NS,TCS.NS``

* Authentication is performed via the ``token`` query parameter (Bearer
  tokens cannot be sent as headers from a browser WebSocket).
* The server pushes a JSON price update every ``PUSH_INTERVAL_SECS`` seconds
  for each requested symbol, sourced from yfinance (15-minute delayed data).
* On client disconnect the loop exits cleanly.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import List

import structlog
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from jose import JWTError

from app.core.security import decode_access_token

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["websocket"])

_PUSH_INTERVAL_SECS = 15
_MAX_SYMBOLS        = 20   # guard against abuse


# ── Price fetcher (runs in thread pool so it doesn't block the event loop) ───

def _fetch_prices_sync(symbols: List[str]) -> List[dict]:
    """Fetch latest quote for each symbol via yfinance and return a list of dicts."""
    try:
        import yfinance as yf  # type: ignore
    except ImportError:
        return []

    result = []
    for sym in symbols:
        try:
            ticker = yf.Ticker(sym)
            info   = ticker.fast_info
            price  = getattr(info, "last_price", None) or getattr(info, "regularMarketPrice", None)
            prev_close = getattr(info, "previous_close", None) or getattr(info, "regularMarketPreviousClose", None)
            if price is None:
                continue
            change_pct = (
                round((price - prev_close) / prev_close * 100, 2)
                if prev_close and prev_close != 0
                else 0.0
            )
            result.append({
                "symbol":     sym,
                "price":      round(float(price), 2),
                "change_pct": change_pct,
                "ts":         int(time.time()),
            })
        except Exception as exc:  # noqa: BLE001
            logger.warning("ws.price_fetch_error", symbol=sym, error=str(exc))
    return result


# ── WebSocket handler ─────────────────────────────────────────────────────────

@router.websocket("/ws/prices")
async def ws_prices(
    websocket: WebSocket,
    token: str = Query(..., description="JWT access token"),
    symbols: str = Query("", description="Comma-separated list of ticker symbols"),
):
    """Stream live price updates for requested symbols.

    The client must supply a valid JWT via the ``token`` query parameter.
    Connection is closed with code 4001 on auth failure.
    """
    # ── Auth ─────────────────────────────────────────────────────────────────
    try:
        payload = decode_access_token(token)
    except JWTError:
        await websocket.close(code=4001, reason="Invalid or expired token.")
        return

    user_id = payload.get("sub", "unknown")

    # ── Parse & sanitise requested symbols ───────────────────────────────────
    requested = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not requested:
        await websocket.close(code=4002, reason="No symbols requested.")
        return
    if len(requested) > _MAX_SYMBOLS:
        requested = requested[:_MAX_SYMBOLS]

    await websocket.accept()
    logger.info("ws.prices.connected", user_id=user_id, symbols=requested)

    # ── Send initial connection ack ───────────────────────────────────────────
    await websocket.send_text(json.dumps({
        "type":    "connected",
        "symbols": requested,
        "interval_secs": _PUSH_INTERVAL_SECS,
    }))

    loop = asyncio.get_event_loop()

    try:
        while True:
            # Fetch prices in a thread to keep the async loop free
            prices = await loop.run_in_executor(None, _fetch_prices_sync, requested)

            if prices:
                await websocket.send_text(json.dumps({
                    "type":   "prices",
                    "data":   prices,
                }))

            # Wait for the next push interval, checking for client disconnect
            try:
                await asyncio.wait_for(
                    websocket.receive_text(),   # raises WebSocketDisconnect on close
                    timeout=_PUSH_INTERVAL_SECS,
                )
                # If client sends anything (e.g. ping), we just ignore it
            except asyncio.TimeoutError:
                pass   # normal — just means no client message; continue streaming

    except WebSocketDisconnect:
        logger.info("ws.prices.disconnected", user_id=user_id)
    except Exception as exc:
        logger.error("ws.prices.error", user_id=user_id, error=str(exc))
        try:
            await websocket.close(code=1011)
        except Exception:
            pass
