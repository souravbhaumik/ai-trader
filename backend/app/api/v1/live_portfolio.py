"""Live portfolio endpoints — Angel One order execution.

POST /portfolio/live/orders              — place a real order via Angel One
GET  /portfolio/live/orders              — list recent orders (from our DB)
DELETE /portfolio/live/orders/{id}       — cancel an open order
GET  /portfolio/live/positions           — fetch live intraday positions from Angel One
GET  /portfolio/live/holdings            — fetch delivery holdings from Angel One

All endpoints require a valid Bearer token.
Only work when user's trading_mode = 'live' and Angel One credentials are set.
"""
from __future__ import annotations

import uuid
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import get_current_user
from app.core.database import get_session
from app.models.user import User
from app.services import live_trade_service as svc

router = APIRouter(prefix="/portfolio", tags=["live-portfolio"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class LiveOrderCreate(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=32)
    direction: str = Field(..., pattern="^(BUY|SELL)$")
    qty: int = Field(..., gt=0)
    order_type: str = Field("MARKET", pattern="^(MARKET|LIMIT)$")
    product_type: str = Field("DELIVERY", pattern="^(DELIVERY|INTRADAY)$")
    price: float = Field(0.0, ge=0)
    signal_id: Optional[uuid.UUID] = None


class LiveOrderOut(BaseModel):
    id: str
    broker_order_id: Optional[str]
    symbol: str
    direction: str
    qty: int
    order_type: str
    product_type: str
    price: float
    status: str
    message: Optional[str] = None
    placed_at: str

    model_config = {"from_attributes": True}


class PositionOut(BaseModel):
    symbol: str
    exchange: str
    product_type: str
    direction: str
    qty: int
    avg_buy_price: float
    ltp: float
    pnl: float
    pnl_pct: float


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/live/orders", response_model=LiveOrderOut, status_code=status.HTTP_201_CREATED)
async def place_live_order(
    payload: LiveOrderCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """Place a real order via Angel One. Requires trading_mode = live."""
    try:
        result = await svc.place_live_order(
            db,
            user_id=current_user.id,
            symbol=payload.symbol.upper(),
            direction=payload.direction,
            qty=payload.qty,
            order_type=payload.order_type,
            product_type=payload.product_type,
            price=payload.price,
            signal_id=payload.signal_id,
        )
        return LiveOrderOut(**result)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))


@router.get("/live/orders", response_model=List[LiveOrderOut])
async def list_live_orders(
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """Return recent live orders for the authenticated user."""
    try:
        rows = await svc.get_live_orders(db, user_id=current_user.id, limit=limit)
        return [LiveOrderOut(**r) for r in rows]
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))


@router.delete("/live/orders/{order_id}", status_code=status.HTTP_200_OK)
async def cancel_live_order(
    order_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """Cancel an open live order."""
    try:
        return await svc.cancel_live_order(db, user_id=current_user.id, order_id=order_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(e))


@router.get("/live/positions", response_model=List[PositionOut])
async def get_live_positions(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """Fetch live intraday positions directly from Angel One."""
    try:
        positions = await svc.get_live_positions(db, user_id=current_user.id)
        return [PositionOut(**p) for p in positions]
    except (ValueError, RuntimeError) as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))


@router.get("/live/holdings", response_model=List[PositionOut])
async def get_live_holdings(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_session),
):
    """Fetch delivery holdings directly from Angel One."""
    try:
        holdings = await svc.get_live_holdings(db, user_id=current_user.id)
        return [PositionOut(**h) for h in holdings]
    except (ValueError, RuntimeError) as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
