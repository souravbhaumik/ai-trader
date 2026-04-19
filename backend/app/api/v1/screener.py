"""Screener API — paginated, filterable Nifty 500 stock screener."""
from __future__ import annotations

from typing import Annotated, Optional

import structlog
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import get_current_user, get_current_user_settings
from app.brokers.factory import get_adapter_for_user
from app.core.database import get_session
from app.models.user import User
from app.models.user_settings import UserSettings
from app.services import screener_service

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/screener", tags=["screener"])


@router.get("")
async def screener(
    user: Annotated[User, Depends(get_current_user)],
    user_settings: Annotated[UserSettings, Depends(get_current_user_settings)],
    session: AsyncSession = Depends(get_session),
    page:     int            = Query(1,      ge=1),
    per_page: int            = Query(50,     ge=1, le=200),
    q:        Optional[str]  = Query(None,   description="Search symbol or name"),
    sector:   Optional[str]  = Query(None,   description="Filter by sector"),
    signal:   Optional[str]  = Query(None,   description="BUY / SELL / HOLD filter"),
    sort_by:  str            = Query("market_cap"),
    sort_dir: str            = Query("desc"),
):
    """Return paginated screener results with live prices from the user's chosen broker."""
    adapter = await get_adapter_for_user(
        str(user.id), user_settings.preferred_broker, session
    )
    result = await screener_service.get_screener_page(
        adapter, session,
        page=page, per_page=per_page,
        q=q, sector=sector, signal_filter=signal,
        sort_by=sort_by, sort_dir=sort_dir,
    )
    result["broker"]       = adapter.broker_name
    result["is_configured"] = adapter.is_credentials_configured()
    if not result["is_configured"] and adapter.broker_name != "yfinance":
        result["warning"] = (
            f"{user_settings.preferred_broker} credentials not configured. "
            "Prices shown from yfinance (15-min delayed)."
        )
    return result


@router.get("/sectors")
async def get_sectors(
    user: Annotated[User, Depends(get_current_user)],
    session: AsyncSession = Depends(get_session),
):
    """Return all distinct sectors from the stock universe."""
    sectors = await screener_service.get_sectors(session)
    return {"sectors": sectors}


@router.get("/universe/search")
async def universe_search(
    user: Annotated[User, Depends(get_current_user)],
    session: AsyncSession = Depends(get_session),
    q: str = Query("", min_length=1, max_length=100),
):
    """Autocomplete search over the stock universe (symbol or name)."""
    from sqlalchemy import text
    pattern = f"%{q}%"
    result = await session.execute(
        text(
            "SELECT symbol, name, exchange FROM stock_universe "
            "WHERE symbol ILIKE :q OR name ILIKE :q "
            "ORDER BY CASE WHEN symbol ILIKE :exact THEN 0 ELSE 1 END, symbol "
            "LIMIT 20"
        ),
        {"q": pattern, "exact": f"{q}%"},
    )
    return [dict(r._mapping) for r in result]
