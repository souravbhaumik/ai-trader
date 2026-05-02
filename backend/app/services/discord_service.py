"""Discord webhook service — Phase 2 / Phase 9.

Sends rich embed alerts to a Discord channel when new signals are generated.
The webhook URL is optional; if ``DISCORD_WEBHOOK_URL`` is not set in the
environment the module silently no-ops, so callers require no branching logic.

Phase 9 enhancements:
- Quick-action deep links to trade confirmation page
- Signal ID for tracking
- Enhanced formatting with timing context
"""
from __future__ import annotations

import urllib.request
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# IST timezone — used for timing labels and embed timestamps
_IST = timezone(timedelta(hours=5, minutes=30))


_COLOR = {"BUY": 0x2ECC71, "SELL": 0xE74C3C}   # green / red


def _webhook_url() -> Optional[str]:
    from app.core.config import settings
    url = settings.discord_webhook_url.strip()
    return url if url else None


def _frontend_url() -> str:
    from app.core.config import settings
    return settings.frontend_url.rstrip("/")


def notify_signal_sync(
    *,
    symbol: str,
    signal_type: str,  # "BUY" | "SELL"
    confidence: float,
    entry: float,
    target: float,
    sl: float,
    signal_id: Optional[str] = None,
) -> None:
    """Send a Discord embed synchronously (used from Celery worker thread).
    
    Phase 9: Includes quick-action deep link for faster trade execution.
    """
    url = _webhook_url()
    if not url:
        return

    color = _COLOR.get(signal_type.upper(), 0x95A5A6)
    rr = round(abs(target - entry) / max(abs(entry - sl), 1e-6), 2)
    
    # Calculate potential gain/loss percentages
    if signal_type == "BUY":
        gain_pct = round((target - entry) / entry * 100, 1)
        risk_pct = round((entry - sl) / entry * 100, 1)
    else:
        gain_pct = round((entry - target) / entry * 100, 1)
        risk_pct = round((sl - entry) / entry * 100, 1)
    
    # Build quick-action URL
    frontend = _frontend_url()
    trade_url = f"{frontend}/paper?action={signal_type.lower()}&symbol={symbol}&price={entry}&target={target}&sl={sl}"
    if signal_id:
        trade_url += f"&signal_id={signal_id}"
    
    # Determine timing context using IST hour
    now = datetime.now(_IST)
    hour = now.hour
    if hour < 9:
        timing = "🌅 Pre-Market Signal"
    elif hour >= 15 and hour < 16:
        timing = "🌆 Near Close Signal"
    elif hour >= 16:
        timing = "🌙 Post-Market Signal"
    else:
        timing = "📊 Intraday Signal"

    payload = {
        "content": f"**{timing}** — Act before market moves! 🚀",
        "embeds": [
            {
                "title": f"{'🟢' if signal_type == 'BUY' else '🔴'} {signal_type} · {symbol}",
                "color": color,
                "description": f"[**⚡ Quick Trade →**]({trade_url})",
                "fields": [
                    {"name": "Entry",       "value": f"₹{entry:,.2f}",            "inline": True},
                    {"name": "Target",      "value": f"₹{target:,.2f} (+{gain_pct}%)", "inline": True},
                    {"name": "Stop-Loss",   "value": f"₹{sl:,.2f} (-{risk_pct}%)", "inline": True},
                    {"name": "Confidence",  "value": f"**{confidence * 100:.0f}%**", "inline": True},
                    {"name": "Risk:Reward", "value": f"1 : {rr}",                 "inline": True},
                    {"name": "Strategy",    "value": "AI Ensemble",               "inline": True},
                ],
                "footer": {"text": f"AI Trader · Signal #{signal_id[:8] if signal_id else 'N/A'}"},
                "timestamp": datetime.now(_IST).isoformat(),
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
    signal_id: Optional[str] = None,
) -> None:
    """Async variant — wraps the sync call in a thread for use from FastAPI."""
    import asyncio
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(
        None,
        lambda: notify_signal_sync(
            symbol=symbol,
            signal_type=signal_type,
            confidence=confidence,
            entry=entry,
            target=target,
            sl=sl,
            signal_id=signal_id,
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
