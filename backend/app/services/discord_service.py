"""Discord webhook service — Phase 2.

Sends rich embed alerts to a Discord channel when new signals are generated.
The webhook URL is optional; if ``DISCORD_WEBHOOK_URL`` is not set in the
environment the module silently no-ops, so callers require no branching logic.
"""
from __future__ import annotations

import urllib.request
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

_COLOR = {"BUY": 0x2ECC71, "SELL": 0xE74C3C}   # green / red


def _webhook_url() -> Optional[str]:
    from app.core.config import settings
    url = settings.discord_webhook_url.strip()
    return url if url else None


def notify_signal_sync(
    *,
    symbol: str,
    signal_type: str,  # "BUY" | "SELL"
    confidence: float,
    entry: float,
    target: float,
    sl: float,
) -> None:
    """Send a Discord embed synchronously (used from Celery worker thread)."""
    url = _webhook_url()
    if not url:
        return

    color = _COLOR.get(signal_type.upper(), 0x95A5A6)
    rr    = round(abs(target - entry) / max(abs(entry - sl), 1e-6), 2)

    payload = {
        "embeds": [
            {
                "title": f"{'🟢' if signal_type == 'BUY' else '🔴'} {signal_type} · {symbol}",
                "color": color,
                "fields": [
                    {"name": "Entry",       "value": f"₹{entry:,.2f}",      "inline": True},
                    {"name": "Target",      "value": f"₹{target:,.2f}",     "inline": True},
                    {"name": "Stop-Loss",   "value": f"₹{sl:,.2f}",         "inline": True},
                    {"name": "Confidence",  "value": f"{confidence * 100:.1f}%", "inline": True},
                    {"name": "R:R",         "value": f"1 : {rr}",           "inline": True},
                    {"name": "Strategy",    "value": "Technical (Phase 2)",  "inline": True},
                ],
                "footer": {"text": "AI Trader · Phase 2 Signal Engine"},
            }
        ]
    }

    data = json.dumps(payload).encode("utf-8")
    req  = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "ai-trader/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            if resp.status not in (200, 204):
                logger.warning("Discord webhook returned %s", resp.status)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Discord webhook failed: %s", exc)


async def notify_signal_async(
    *,
    symbol: str,
    signal_type: str,
    confidence: float,
    entry: float,
    target: float,
    sl: float,
) -> None:
    """Async variant — wraps the sync call in a thread for use from FastAPI."""
    import asyncio
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None,
        lambda: notify_signal_sync(
            symbol=symbol,
            signal_type=signal_type,
            confidence=confidence,
            entry=entry,
            target=target,
            sl=sl,
        ),
    )


def notify_trade_fill_sync(
    *,
    symbol: str,
    direction: str,       # "BUY" | "SELL"
    qty: int,
    order_type: str,
    broker_order_id: str,
    status: str,
    price: float = 0.0,
) -> None:
    """Send a Discord embed when a live order is placed / filled."""
    url = _webhook_url()
    if not url:
        return

    color = _COLOR.get(direction.upper(), 0x95A5A6)
    emoji = "🟢" if direction.upper() == "BUY" else "🔴"
    price_str = f"₹{price:,.2f}" if price else "MARKET"

    payload = {
        "embeds": [
            {
                "title": f"{emoji} LIVE ORDER · {direction.upper()} {symbol}",
                "color": color,
                "fields": [
                    {"name": "Symbol",      "value": symbol,             "inline": True},
                    {"name": "Direction",   "value": direction.upper(),  "inline": True},
                    {"name": "Qty",         "value": str(qty),           "inline": True},
                    {"name": "Order Type",  "value": order_type.upper(), "inline": True},
                    {"name": "Price",       "value": price_str,          "inline": True},
                    {"name": "Status",      "value": status,             "inline": True},
                    {"name": "Order ID",    "value": broker_order_id or "—", "inline": False},
                ],
                "footer": {"text": "AI Trader · Live Execution"},
            }
        ]
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "ai-trader/1.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            if resp.status not in (200, 204):
                logger.warning("Discord trade webhook returned %s", resp.status)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Discord trade webhook failed: %s", exc)
