"""Paper portfolio endpoints — Phase 5.

GET  /portfolio/paper/summary     — cash balance, P&L, win rate
GET  /portfolio/paper/positions   — open trades
GET  /portfolio/paper/history     — closed trades (paginated)
POST /portfolio/paper/orders      — open a manual paper trade
POST /portfolio/paper/orders/{id}/close — close an open trade

All endpoints require a valid Bearer token (any authenticated user).
"""
from __future__ import annotations

import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import get_current_user
from app.core.database import get_session
from app.models.user import User
from app.schemas.paper_trade import (
    PaperOrderClose,
    PaperOrderCreate,
    PaperTradeOut,
    PortfolioSummary,
)
from app.services import paper_trade_service as svc

router = APIRouter(prefix="/portfolio", tags=["portfolio"])


# ── Summary ───────────────────────────────────────────────────────────────────

@router.get("/paper/summary", response_model=PortfolioSummary)
async def get_paper_summary(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Aggregated paper portfolio stats for the authenticated user."""
    data = await svc.get_summary(session, user_id=current_user.id)
    return PortfolioSummary(**data)


# ── Open positions ────────────────────────────────────────────────────────────

@router.get("/paper/positions", response_model=List[PaperTradeOut])
async def get_open_positions(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """All currently open paper trades for the authenticated user."""
    result = await session.execute(
        text("""
            SELECT id, user_id, symbol, direction, qty,
                   entry_price, target_price, stop_loss, exit_price,
                   signal_id, status, pnl, pnl_pct,
                   entry_at, exit_at, notes
            FROM   paper_trades
            WHERE  user_id = :uid AND status = 'open'
            ORDER  BY entry_at DESC
        """),
        {"uid": str(current_user.id)},
    )
    rows = result.mappings().all()
    return [PaperTradeOut.model_validate(dict(r)) for r in rows]


# ── Trade history ─────────────────────────────────────────────────────────────

@router.get("/paper/history", response_model=List[PaperTradeOut])
async def get_trade_history(
    limit: int = Query(50, ge=1, le=500),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Closed paper trades for the authenticated user, newest first."""
    result = await session.execute(
        text("""
            SELECT id, user_id, symbol, direction, qty,
                   entry_price, target_price, stop_loss, exit_price,
                   signal_id, status, pnl, pnl_pct,
                   entry_at, exit_at, notes
            FROM   paper_trades
            WHERE  user_id = :uid AND status != 'open'
            ORDER  BY exit_at DESC NULLS LAST
            LIMIT  :limit
        """),
        {"uid": str(current_user.id), "limit": limit},
    )
    rows = result.mappings().all()
    return [PaperTradeOut.model_validate(dict(r)) for r in rows]


# ── Place order ───────────────────────────────────────────────────────────────

@router.post("/paper/orders", response_model=dict, status_code=status.HTTP_201_CREATED)
async def place_paper_order(
    body: PaperOrderCreate,
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Manually open a paper trade.

    Supports idempotency via ``Idempotency-Key`` header to prevent duplicate
    orders from network retries.
    """
    # ── Idempotency check ─────────────────────────────────────────────────────
    idem_key = request.headers.get("Idempotency-Key")
    if idem_key:
        from app.core.redis_client import get_redis
        redis = get_redis()
        cache_key = f"idem:paper:{current_user.id}:{idem_key}"
        cached = await redis.get(cache_key)
        if cached:
            import json
            return json.loads(cached)

    try:
        result = await svc.open_trade(
            session,
            user_id=current_user.id,
            symbol=body.symbol,
            direction=body.direction,
            qty=body.qty,
            entry_price=body.entry_price,
            target_price=body.target_price,
            stop_loss=body.stop_loss,
            notes=body.notes,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

    # Cache result for idempotency (5 min TTL)
    if idem_key:
        import json
        from app.core.redis_client import get_redis
        redis = get_redis()
        cache_key = f"idem:paper:{current_user.id}:{idem_key}"
        await redis.setex(cache_key, 300, json.dumps(result))

    return result


# ── Close order ───────────────────────────────────────────────────────────────

@router.post("/paper/orders/{trade_id}/close", response_model=dict)
async def close_paper_order(
    trade_id: uuid.UUID,
    body: PaperOrderClose = PaperOrderClose(),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Close an open paper trade. Fetches live price if exit_price not provided."""
    try:
        result = await svc.close_trade(
            session,
            trade_id=trade_id,
            user_id=current_user.id,
            exit_price=body.exit_price,
            status=body.status,
        )
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))
    return result
