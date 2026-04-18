"""Live trading service — Angel One order management.

Handles placing and tracking REAL orders via the Angel One SmartAPI.
Only executes when the user's trading_mode = 'live'.

Safety guarantees:
  - Always re-reads trading_mode from DB before placing any order.
  - Requires user to have Angel One credentials stored and verified.
  - All orders are recorded in `live_orders` table before API call.
  - Returns a structured result; never silently swallows errors.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.base import OrderResult

logger = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _get_user_adapter(user_id: uuid.UUID, db: AsyncSession):
    """Load user's broker adapter (connected). Raises if not in live mode."""
    row = await db.execute(
        text("""
            SELECT us.trading_mode, us.preferred_broker,
                   bc.api_key, bc.api_secret, bc.client_id, bc.totp_secret
            FROM   user_settings us
            LEFT JOIN broker_credentials bc
                   ON bc.user_id = us.user_id AND bc.broker_name = us.preferred_broker
            WHERE  us.user_id = :uid
        """),
        {"uid": str(user_id)},
    )
    row = row.first()
    if not row:
        raise ValueError("User settings not found")

    if row.trading_mode != "live":
        raise ValueError("User is not in live trading mode")

    if row.preferred_broker != "angel_one":
        raise ValueError(f"Live trading is only supported with Angel One (got {row.preferred_broker})")

    from app.core.security import decrypt_field
    from app.brokers.angel_one import AngelOneAdapter

    adapter = AngelOneAdapter(
        api_key=decrypt_field(row.api_key) if row.api_key else None,
        client_id=decrypt_field(row.client_id) if row.client_id else None,
        password=decrypt_field(row.api_secret) if row.api_secret else None,
        totp_secret=decrypt_field(row.totp_secret) if row.totp_secret else None,
    )
    if not adapter.is_credentials_configured():
        raise ValueError("Angel One credentials not configured. Add them in Settings → Broker.")

    await adapter.connect()
    if not adapter._smart_api:
        raise RuntimeError("Angel One authentication failed. Check your credentials.")

    return adapter


# ── Public API ─────────────────────────────────────────────────────────────────

async def place_live_order(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    symbol: str,
    direction: str,
    qty: int,
    order_type: str = "MARKET",
    product_type: str = "DELIVERY",
    price: float = 0.0,
    signal_id: Optional[uuid.UUID] = None,
) -> dict:
    """Place a live order via Angel One and record it in live_orders.

    Returns the stored live_order record dict.
    Raises ValueError / RuntimeError with human-readable messages on failure.
    """
    adapter = await _get_user_adapter(user_id, db)

    # Pre-insert with PENDING status so we have a DB record even if API crashes
    order_id = uuid.uuid4()
    now_ts = _now()
    await db.execute(
        text("""
            INSERT INTO live_orders
                (id, user_id, symbol, direction, qty, order_type, product_type,
                 price, status, signal_id, placed_at, updated_at)
            VALUES
                (:id, :uid, :sym, :dir, :qty, :ot, :pt,
                 :price, 'PENDING', :sig, :now, :now)
        """),
        {
            "id":    str(order_id),
            "uid":   str(user_id),
            "sym":   symbol,
            "dir":   direction.upper(),
            "qty":   qty,
            "ot":    order_type.upper(),
            "pt":    product_type.upper(),
            "price": price,
            "sig":   str(signal_id) if signal_id else None,
            "now":   now_ts,
        },
    )
    await db.commit()

    # Place the order
    result: OrderResult = await adapter.place_order(
        symbol=symbol,
        direction=direction,
        qty=qty,
        order_type=order_type,
        product_type=product_type,
        price=price,
    )

    # Update DB with broker's order ID and status
    await db.execute(
        text("""
            UPDATE live_orders
            SET    broker_order_id = :boid,
                   broker_status   = :bstat,
                   status          = :status,
                   updated_at      = :now
            WHERE  id = :id
        """),
        {
            "boid":  result.broker_order_id,
            "bstat": result.status,
            "status": result.status,
            "now":   _now(),
            "id":    str(order_id),
        },
    )
    await db.commit()

    # Discord notification
    try:
        from app.services.discord_service import notify_trade_fill_sync
        notify_trade_fill_sync(
            symbol=symbol,
            direction=direction,
            qty=qty,
            order_type=order_type,
            broker_order_id=result.broker_order_id,
            status=result.status,
            price=price,
        )
    except Exception:  # noqa: BLE001
        pass   # Discord is optional

    await adapter.disconnect()

    return {
        "id": str(order_id),
        "broker_order_id": result.broker_order_id,
        "symbol": symbol,
        "direction": direction.upper(),
        "qty": qty,
        "order_type": order_type.upper(),
        "product_type": product_type.upper(),
        "price": price,
        "status": result.status,
        "message": result.message,
        "placed_at": now_ts.isoformat(),
    }


async def get_live_positions(db: AsyncSession, *, user_id: uuid.UUID) -> list:
    """Fetch live positions directly from Angel One."""
    adapter = await _get_user_adapter(user_id, db)
    positions = await adapter.get_positions()
    await adapter.disconnect()
    return [
        {
            "symbol": p.symbol,
            "exchange": p.exchange,
            "product_type": p.product_type,
            "direction": p.direction,
            "qty": p.qty,
            "avg_buy_price": p.avg_buy_price,
            "ltp": p.ltp,
            "pnl": p.pnl,
            "pnl_pct": p.pnl_pct,
        }
        for p in positions
    ]


async def get_live_holdings(db: AsyncSession, *, user_id: uuid.UUID) -> list:
    """Fetch delivery holdings directly from Angel One."""
    adapter = await _get_user_adapter(user_id, db)
    holdings = await adapter.get_holdings()
    await adapter.disconnect()
    return [
        {
            "symbol": h.symbol,
            "exchange": h.exchange,
            "product_type": h.product_type,
            "direction": h.direction,
            "qty": h.qty,
            "avg_buy_price": h.avg_buy_price,
            "ltp": h.ltp,
            "pnl": h.pnl,
            "pnl_pct": h.pnl_pct,
        }
        for h in holdings
    ]


async def get_live_orders(db: AsyncSession, *, user_id: uuid.UUID, limit: int = 50) -> list:
    """Return recent live orders from our DB."""
    result = await db.execute(
        text("""
            SELECT id, broker_order_id, symbol, direction, qty,
                   order_type, product_type, price, status,
                   broker_status, signal_id, placed_at, updated_at
            FROM   live_orders
            WHERE  user_id = :uid
            ORDER  BY placed_at DESC
            LIMIT  :lim
        """),
        {"uid": str(user_id), "lim": limit},
    )
    rows = result.mappings().all()
    return [dict(r) for r in rows]


async def cancel_live_order(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    order_id: uuid.UUID,
) -> dict:
    """Cancel a live order by our internal order ID."""
    # Verify ownership
    row = await db.execute(
        text("SELECT broker_order_id, status FROM live_orders WHERE id = :id AND user_id = :uid"),
        {"id": str(order_id), "uid": str(user_id)},
    )
    row = row.first()
    if not row:
        raise ValueError("Order not found")
    if row.status in ("COMPLETE", "CANCELLED", "REJECTED"):
        raise ValueError(f"Order already {row.status.lower()}")

    adapter = await _get_user_adapter(user_id, db)
    success = await adapter.cancel_order(row.broker_order_id)
    await adapter.disconnect()

    new_status = "CANCELLED" if success else row.status
    await db.execute(
        text("UPDATE live_orders SET status = :s, updated_at = :now WHERE id = :id"),
        {"s": new_status, "now": _now(), "id": str(order_id)},
    )
    await db.commit()

    return {"cancelled": success, "status": new_status}
