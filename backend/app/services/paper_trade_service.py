"""Paper trading service — Phase 5.

Handles all business logic for paper trades:
- auto_paper_trade()  called from signal_generator (sync/Celery)
- open_trade()        called from portfolio API (async/FastAPI)
- close_trade()       called from portfolio API (async/FastAPI)
- get_summary()       called from portfolio API (async/FastAPI)

Paper trades simulate real execution using yfinance prices as needed.
Cash balance is maintained in user_settings.paper_balance.
"""
from __future__ import annotations

import uuid
import logging
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_AUTO_TRADE_ENV = "PAPER_AUTO_TRADE"   # set to "true" to enable auto-execution


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ── Sync helpers (used by Celery signal_generator) ───────────────────────────

def auto_paper_trade(
    session,          # sync SQLAlchemy Session
    *,
    signal_id: str,
    symbol: str,
    direction: str,
    entry_price: float,
    target_price: float,
    stop_loss: float,
) -> int:
    """Place paper trades for all users who have trading_mode='paper'.

    Called inside the signal_generator task after a signal is committed.
    Returns the number of paper trades placed.
    """
    import os
    if os.getenv(_AUTO_TRADE_ENV, "false").lower() not in ("1", "true", "yes"):
        return 0

    try:
        users = session.execute(
            text("""
                SELECT us.user_id, us.paper_balance, us.max_position_pct
                FROM   user_settings us
                WHERE  us.trading_mode = 'paper'
            """)
        ).fetchall()

        placed = 0
        now_ts = _now()

        for row in users:
            user_id, paper_balance, max_pos_pct = row
            paper_balance = Decimal(str(paper_balance))
            max_pos_pct   = Decimal(str(max_pos_pct))
            entry          = Decimal(str(entry_price))

            if entry <= 0:
                continue

            # Position size: up to max_position_pct% of balance
            max_value = paper_balance * max_pos_pct / Decimal("100")
            qty = int(max_value / entry)
            if qty <= 0:
                continue

            cost = entry * qty
            if cost > paper_balance:
                continue          # not enough balance

            trade_id = str(uuid.uuid4())
            session.execute(
                text("""
                    INSERT INTO paper_trades
                        (id, user_id, symbol, direction, qty,
                         entry_price, target_price, stop_loss,
                         signal_id, status, entry_at, created_at)
                    VALUES
                        (:id, :user_id, :symbol, :direction, :qty,
                         :entry_price, :target_price, :stop_loss,
                         :signal_id, 'open', :now, :now)
                """),
                {
                    "id":           trade_id,
                    "user_id":      str(user_id),
                    "symbol":       symbol,
                    "direction":    direction,
                    "qty":          qty,
                    "entry_price":  float(entry),
                    "target_price": target_price,
                    "stop_loss":    stop_loss,
                    "signal_id":    signal_id,
                    "now":          now_ts,
                },
            )

            # Deduct cost from paper balance
            session.execute(
                text("""
                    UPDATE user_settings
                    SET    paper_balance = paper_balance - :cost,
                           updated_at   = :now
                    WHERE  user_id = :user_id
                """),
                {"cost": float(cost), "now": now_ts, "user_id": str(user_id)},
            )
            placed += 1

        return placed

    except Exception as exc:  # noqa: BLE001
        logger.warning("paper_trade.auto_failed: %s", exc)
        return 0


# ── Async helpers (used by FastAPI endpoints) ─────────────────────────────────

async def open_trade(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    symbol: str,
    direction: str,
    qty: int,
    entry_price: Decimal,
    target_price: Optional[Decimal],
    stop_loss: Optional[Decimal],
    signal_id: Optional[uuid.UUID] = None,
    notes: Optional[str] = None,
) -> dict:
    """Place a manual paper trade. Raises ValueError on insufficient balance."""
    cost = entry_price * qty

    # Check balance
    row = await session.execute(
        text("SELECT paper_balance FROM user_settings WHERE user_id = :uid"),
        {"uid": str(user_id)},
    )
    row = row.first()
    if row is None:
        raise ValueError("User settings not found.")
    balance = Decimal(str(row[0]))
    if cost > balance:
        raise ValueError(
            f"Insufficient paper balance ₹{balance:,.2f} for trade cost ₹{cost:,.2f}."
        )

    now_ts   = _now()
    trade_id = uuid.uuid4()

    await session.execute(
        text("""
            INSERT INTO paper_trades
                (id, user_id, symbol, direction, qty,
                 entry_price, target_price, stop_loss,
                 signal_id, status, notes, entry_at, created_at)
            VALUES
                (:id, :user_id, :symbol, :direction, :qty,
                 :entry_price, :target_price, :stop_loss,
                 :signal_id, 'open', :notes, :now, :now)
        """),
        {
            "id":           str(trade_id),
            "user_id":      str(user_id),
            "symbol":       symbol,
            "direction":    direction,
            "qty":          qty,
            "entry_price":  float(entry_price),
            "target_price": float(target_price) if target_price else None,
            "stop_loss":    float(stop_loss)    if stop_loss    else None,
            "signal_id":    str(signal_id)      if signal_id    else None,
            "notes":        notes,
            "now":          now_ts,
        },
    )

    await session.execute(
        text("""
            UPDATE user_settings
            SET    paper_balance = paper_balance - :cost,
                   updated_at   = :now
            WHERE  user_id = :uid
        """),
        {"cost": float(cost), "now": now_ts, "uid": str(user_id)},
    )

    await session.commit()
    return {"id": str(trade_id), "symbol": symbol, "direction": direction,
            "qty": qty, "entry_price": float(entry_price), "cost": float(cost)}


async def close_trade(
    session: AsyncSession,
    *,
    trade_id: uuid.UUID,
    user_id: uuid.UUID,
    exit_price: Optional[Decimal] = None,
    status: str = "closed",
) -> dict:
    """Close an open paper trade. Fetches current price from yfinance if exit_price not given."""
    row = await session.execute(
        text("""
            SELECT symbol, direction, qty, entry_price, status
            FROM   paper_trades
            WHERE  id = :id AND user_id = :uid
        """),
        {"id": str(trade_id), "uid": str(user_id)},
    )
    trade = row.first()
    if trade is None:
        raise ValueError("Trade not found.")
    symbol, direction, qty, entry_price, cur_status = trade
    if cur_status != "open":
        raise ValueError(f"Trade is already {cur_status}.")

    entry_price = Decimal(str(entry_price))

    if exit_price is None:
        # Fetch latest close from yfinance (best-effort; fall back to entry)
        try:
            import yfinance as yf
            ticker = yf.Ticker(symbol)
            hist   = ticker.history(period="2d")
            if not hist.empty:
                exit_price = Decimal(str(round(float(hist["Close"].iloc[-1]), 4)))
        except Exception:  # noqa: BLE001
            pass
        if exit_price is None:
            exit_price = entry_price   # flat P&L if price unavailable

    # P&L: BUY profits when price rises; SELL profits when price falls
    if direction == "BUY":
        pnl = (exit_price - entry_price) * qty
    else:
        pnl = (entry_price - exit_price) * qty

    entry_value = entry_price * qty
    pnl_pct     = (pnl / entry_value * 100) if entry_value else Decimal("0")
    now_ts      = _now()
    proceeds    = entry_value + pnl    # cash returned on close

    await session.execute(
        text("""
            UPDATE paper_trades
            SET    exit_price = :exit_price,
                   status     = :status,
                   pnl        = :pnl,
                   pnl_pct    = :pnl_pct,
                   exit_at    = :now
            WHERE  id = :id
        """),
        {
            "exit_price": float(exit_price),
            "status":     status,
            "pnl":        float(pnl),
            "pnl_pct":    float(pnl_pct),
            "now":        now_ts,
            "id":         str(trade_id),
        },
    )

    await session.execute(
        text("""
            UPDATE user_settings
            SET    paper_balance = paper_balance + :proceeds,
                   updated_at   = :now
            WHERE  user_id = :uid
        """),
        {"proceeds": float(proceeds), "now": now_ts, "uid": str(user_id)},
    )

    await session.commit()
    return {
        "id":         str(trade_id),
        "exit_price": float(exit_price),
        "pnl":        float(pnl),
        "pnl_pct":    float(pnl_pct),
        "status":     status,
    }


async def get_summary(session: AsyncSession, *, user_id: uuid.UUID) -> dict:
    """Return aggregated portfolio stats for a user."""
    balance_row = await session.execute(
        text("SELECT paper_balance FROM user_settings WHERE user_id = :uid"),
        {"uid": str(user_id)},
    )
    balance_row = balance_row.first()
    cash_balance = Decimal(str(balance_row[0])) if balance_row else Decimal("0")

    stats = await session.execute(
        text("""
            SELECT
                COUNT(*) FILTER (WHERE status = 'open')                     AS open_positions,
                COALESCE(SUM(qty * entry_price) FILTER (WHERE status = 'open'), 0) AS open_value,
                COALESCE(SUM(pnl) FILTER (WHERE status != 'open'), 0)        AS realized_pnl,
                COUNT(*) FILTER (WHERE status != 'open')                     AS closed_trades,
                COUNT(*)                                                      AS total_trades,
                COUNT(*) FILTER (WHERE status != 'open' AND pnl > 0)         AS wins
            FROM paper_trades
            WHERE user_id = :uid
        """),
        {"uid": str(user_id)},
    )
    r = stats.first()
    open_positions = int(r[0] or 0)
    open_value     = Decimal(str(r[1] or 0))
    realized_pnl   = Decimal(str(r[2] or 0))
    closed_trades  = int(r[3] or 0)
    total_trades   = int(r[4] or 0)
    wins           = int(r[5] or 0)
    win_rate       = round(wins / closed_trades * 100, 1) if closed_trades > 0 else None

    return {
        "cash_balance":   cash_balance,
        "open_positions": open_positions,
        "open_value":     open_value,
        "realized_pnl":   realized_pnl,
        "total_trades":   total_trades,
        "closed_trades":  closed_trades,
        "win_rate":       win_rate,
    }
