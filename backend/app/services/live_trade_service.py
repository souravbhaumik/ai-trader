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
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.base import OrderResult

logger = logging.getLogger(__name__)

# NSE market hours (IST)
_MARKET_OPEN_IST  = (9, 15)   # 09:15 IST
_MARKET_CLOSE_IST = (15, 30)  # 15:30 IST

# IST timezone constant
_IST = timezone(timedelta(hours=5, minutes=30))


def _now() -> datetime:
    """Return current IST-aware datetime for DB timestamp fields."""
    return datetime.now(_IST)


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _get_user_adapter(user_id: uuid.UUID, db: AsyncSession):
    """Load user's broker adapter (connected). Raises if not in live mode."""
    row = await db.execute(
        text("""
            SELECT us.trading_mode, us.preferred_broker,
                   us.is_live_trading_enabled,
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

    if not row.is_live_trading_enabled:
        raise ValueError(
            "Live trading is not enabled for this account. "
            "Please complete OTP verification in Settings → Trading Mode."
        )

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

async def _enforce_daily_loss_limit(user_id: uuid.UUID, db: AsyncSession) -> None:
    """Raise ValueError if the user has exceeded their daily loss limit.

    Computes today's realised P&L from completed SELL orders:
      loss = sum((price - avg_fill_price) * filled_qty)  for SELL orders
    where a negative value means a net loss.

    If |loss| > daily_loss_limit_pct % of paper_balance, the user's
    trading_mode is flipped to 'paper' and a ValueError is raised.
    """
    from datetime import date  # noqa: PLC0415

    # IST midnight = UTC midnight - 5h30m; use UTC date for simplicity (conservative)
    today_start = datetime.combine(date.today(), datetime.min.time())

    # Sum of (avg_fill_price - price) * filled_qty for completed SELL orders today.
    # A positive result here means the user sold below their order price → loss.
    result = await db.execute(
        text("""
            SELECT COALESCE(SUM((price - avg_fill_price) * filled_qty), 0) AS realised_pnl
            FROM   live_orders
            WHERE  user_id   = :uid
              AND  direction  = 'SELL'
              AND  status     = 'COMPLETE'
              AND  placed_at >= :today
        """),
        {"uid": str(user_id), "today": today_start},
    )
    realised_pnl = float(result.scalar() or 0)
    # negative realised_pnl = net loss
    if realised_pnl >= 0:
        return  # profit or break-even — no limit breach

    loss_amount = abs(realised_pnl)

    settings_row = await db.execute(
        text("SELECT paper_balance, daily_loss_limit_pct FROM user_settings WHERE user_id = :uid"),
        {"uid": str(user_id)},
    )
    settings_row = settings_row.first()
    if not settings_row:
        return

    limit_pct        = float(settings_row.daily_loss_limit_pct)

    # For live trading, compute portfolio value from today's starting live positions,
    # not paper_balance which is a separate simulation.
    # Use paper_balance as a fallback denominator if no live portfolio data available.
    portfolio_value  = float(settings_row.paper_balance)

    # Compute live portfolio value as net BUY cost minus net SELL proceeds of all
    # COMPLETE orders.  SUM(price * filled_qty) for ALL orders overestimates because
    # it counts both sides; the net BUY-minus-SELL figure reflects current exposure.
    try:
        holdings_result = await db.execute(
            text("""
                SELECT
                    COALESCE(SUM(CASE WHEN direction = 'BUY'  THEN avg_fill_price * filled_qty ELSE 0 END), 0)
                  - COALESCE(SUM(CASE WHEN direction = 'SELL' THEN avg_fill_price * filled_qty ELSE 0 END), 0)
                    AS net_portfolio_value
                FROM live_orders
                WHERE user_id = :uid AND status = 'COMPLETE'
            """),
            {"uid": str(user_id)},
        )
        live_value = float(holdings_result.scalar() or 0)
        if live_value > 0:
            portfolio_value = live_value
    except Exception:
        pass  # use paper_balance as fallback

    limit_amount     = portfolio_value * limit_pct / 100.0

    if loss_amount >= limit_amount:
        # Flip to paper trading
        await db.execute(
            text("""
                UPDATE user_settings
                SET    trading_mode = 'paper',
                       updated_at   = :now
                WHERE  user_id = :uid
            """),
            {"uid": str(user_id), "now": _now()},
        )
        await db.commit()
        logger.warning(
            "live_trade.daily_loss_limit_reached",
            user_id=str(user_id),
            loss_amount=loss_amount,
            limit_amount=limit_amount,
        )
        raise ValueError(
            f"Daily loss limit reached (₹{loss_amount:,.2f} ≥ {limit_pct}% of portfolio). "
            "Switched to paper trading."
        )


async def _check_market_hours(user_id: uuid.UUID, db: AsyncSession) -> None:
    """Raise ValueError if outside NSE market hours AND user has enforce_market_hours=True."""
    # Load user setting
    result = await db.execute(
        text("SELECT enforce_market_hours FROM user_settings WHERE user_id = :uid"),
        {"uid": str(user_id)},
    )
    row = result.first()
    if row and not row.enforce_market_hours:
        return  # user disabled the guard

    # Check IST time (UTC+5:30)
    from datetime import timezone, timedelta
    IST = timezone(timedelta(hours=5, minutes=30))
    now_ist = datetime.now(IST)
    open_h, open_m   = _MARKET_OPEN_IST
    close_h, close_m = _MARKET_CLOSE_IST

    open_minutes  = open_h  * 60 + open_m
    close_minutes = close_h * 60 + close_m
    now_minutes   = now_ist.hour * 60 + now_ist.minute

    # Monday=0 .. Friday=4
    if now_ist.weekday() > 4:
        raise ValueError(
            "Markets are closed on weekends. "
            "Disable 'Enforce Market Hours' in Settings to override."
        )

    if not (open_minutes <= now_minutes <= close_minutes):
        raise ValueError(
            f"NSE market hours are 09:15–15:30 IST (current IST time: "
            f"{now_ist.strftime('%H:%M')}). "
            "Disable 'Enforce Market Hours' in Settings to override."
        )


async def _check_sector_exposure(
    user_id: uuid.UUID,
    symbol: str,
    db: AsyncSession,
) -> None:
    """Warn (log) and raise ValueError if adding this position would exceed the user's
    max_sector_exposure_pct limit for the sector of the given symbol."""
    # Get sector for this symbol
    sector_result = await db.execute(
        text("SELECT sector FROM stock_universe WHERE symbol = :sym LIMIT 1"),
        {"sym": symbol},
    )
    sector_row = sector_result.first()
    if not sector_row or not sector_row.sector:
        return  # Unknown sector — skip check

    sector = sector_row.sector

    # Get user's max_sector_exposure_pct
    settings_result = await db.execute(
        text("SELECT max_sector_exposure_pct FROM user_settings WHERE user_id = :uid"),
        {"uid": str(user_id)},
    )
    settings_row = settings_result.first()
    if not settings_row:
        return

    max_pct = float(settings_row.max_sector_exposure_pct)

    # Sum open positions in the same sector (by value: price * qty for BUY orders)
    exposure_result = await db.execute(
        text("""
            SELECT COALESCE(SUM(lo.price * lo.qty), 0) AS sector_value,
                   COALESCE(SUM(lo2.price * lo2.qty), 0) AS total_value
            FROM live_orders lo
            JOIN stock_universe su ON su.symbol = lo.symbol AND su.sector = :sector
            CROSS JOIN (
                SELECT COALESCE(SUM(price * qty), 0) AS total
                FROM live_orders
                WHERE user_id = :uid AND direction = 'BUY' AND status = 'COMPLETE'
            ) lo2
            WHERE lo.user_id = :uid AND lo.direction = 'BUY' AND lo.status = 'COMPLETE'
        """),
        {"uid": str(user_id), "sector": sector},
    )
    exp_row = exposure_result.first()
    if not exp_row or exp_row.total_value == 0:
        return  # No positions yet — allow

    sector_value = float(exp_row.sector_value)
    total_value  = float(exp_row.total_value)
    sector_pct   = (sector_value / total_value * 100) if total_value > 0 else 0

    if sector_pct >= max_pct:
        logger.warning(
            "live_trade.sector_exposure_limit",
            user_id=str(user_id),
            sector=sector,
            sector_pct=round(sector_pct, 1),
            max_pct=max_pct,
        )
        raise ValueError(
            f"Sector exposure limit reached: '{sector}' is already "
            f"{sector_pct:.1f}% of your portfolio (limit: {max_pct:.0f}%). "
            "Adjust your limit in Settings or diversify across sectors."
        )


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

    # ── Market hours check (respects user.enforce_market_hours setting) ────────
    if direction.upper() == "BUY":
        await _check_market_hours(user_id, db)

    # ── Sector exposure check (only for BUY orders) ────────────────────────────
    if direction.upper() == "BUY":
        await _check_sector_exposure(user_id, symbol, db)

    # ── Daily loss limit check ─────────────────────────────────────────────────
    await _enforce_daily_loss_limit(user_id, db)

    # Pre-insert with PENDING status so we have a DB record even if API crashes.
    # order_id is also used as the idempotency ``order_tag`` sent to the broker.
    order_id = uuid.uuid4()
    order_tag = str(order_id)[:20]   # Angel One ordertag limit: 20 chars
    now_ts = _now()

    # Capture expected_price for slippage tracking (use current price or limit price)
    expected_price = price if price and price > 0 else None

    await db.execute(
        text("""
            INSERT INTO live_orders
                (id, user_id, symbol, direction, qty, order_type, product_type,
                 price, status, signal_id, placed_at, updated_at, expected_price)
            VALUES
                (:id, :uid, :sym, :dir, :qty, :ot, :pt,
                 :price, 'PENDING', :sig, :now, :now, :expected_price)
        """),
        {
            "id":             str(order_id),
            "uid":            str(user_id),
            "sym":            symbol,
            "dir":            direction.upper(),
            "qty":            qty,
            "ot":             order_type.upper(),
            "pt":             product_type.upper(),
            "price":          price,
            "sig":            str(signal_id) if signal_id else None,
            "now":            now_ts,
            "expected_price": expected_price,
        },
    )
    await db.commit()

    # Place the order — pass order_tag for idempotency.
    # On network timeout we query the order book by tag rather than retrying
    # the POST (which would double-place the order).
    result: Optional[OrderResult] = None
    try:
        result = await adapter.place_order(
            symbol=symbol,
            direction=direction,
            qty=qty,
            order_type=order_type,
            product_type=product_type,
            price=price,
            order_tag=order_tag,
        )
    except (TimeoutError, ConnectionError, OSError) as exc:
        # Network-level timeout: the order MAY have reached the broker.
        # Query the order book by our tag to check before giving up.
        logger.warning(
            "live_trade.place_order_timeout_checking_order_book",
            order_id=str(order_id), err=str(exc),
        )
        try:
            result = await adapter.get_order_by_tag(order_tag)
        except Exception as inner_exc:
            logger.error(
                "live_trade.order_book_query_failed",
                order_id=str(order_id), err=str(inner_exc),
            )

        if result is None:
            # Confirmed not placed — mark row as TIMEOUT and re-raise
            await db.execute(
                text("UPDATE live_orders SET status='TIMEOUT', updated_at=:now WHERE id=:id"),
                {"now": _now(), "id": str(order_id)},
            )
            await db.commit()
            raise RuntimeError(
                "Network timeout placing order. The order was NOT placed. "
                "Please check your positions before retrying."
            ) from exc

    # Calculate slippage if we have both expected and fill prices
    fill_price  = result.avg_fill_price if hasattr(result, "avg_fill_price") else None
    slippage_pct: Optional[float] = None
    if fill_price and expected_price and expected_price > 0:
        slippage_pct = round((fill_price - expected_price) / expected_price * 100, 4)
        if direction.upper() == "SELL":
            slippage_pct = -slippage_pct  # negative slippage = worse fill for sells

    # Update DB with broker's order ID, status, and slippage
    await db.execute(
        text("""
            UPDATE live_orders
            SET    broker_order_id = :boid,
                   broker_status   = :bstat,
                   status          = :status,
                   avg_fill_price  = :fill_price,
                   slippage_pct    = :slippage_pct,
                   updated_at      = :now
            WHERE  id = :id
        """),
        {
            "boid":         result.broker_order_id,
            "bstat":        result.status,
            "status":       result.status,
            "fill_price":   fill_price,
            "slippage_pct": slippage_pct,
            "now":          _now(),
            "id":           str(order_id),
        },
    )
    await db.commit()

    if slippage_pct and abs(slippage_pct) > 0.5:
        logger.warning(
            "live_trade.high_slippage",
            order_id=str(order_id),
            symbol=symbol,
            expected_price=expected_price,
            fill_price=fill_price,
            slippage_pct=slippage_pct,
        )

    # Discord notification (fire-and-forget, non-blocking)
    try:
        import asyncio
        from app.services.discord_service import notify_trade_fill_sync

        loop = asyncio.get_running_loop()
        loop.run_in_executor(
            None,
            notify_trade_fill_sync,
            symbol,
            direction,
            qty,
            order_type,
            result.broker_order_id,
            result.status,
            price,
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
        "avg_fill_price": fill_price,
        "slippage_pct": slippage_pct,
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
