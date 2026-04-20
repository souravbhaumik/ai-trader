"""Prices API — market quotes, indices, historical OHLCV."""
from typing import Annotated, List

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import get_current_user, get_current_user_settings
from app.brokers.factory import get_adapter_for_user
from app.core.config import settings
from app.core.database import get_session
from app.core.rate_limiter import limiter
from app.models.user import User
from app.models.user_settings import UserSettings
from app.services import price_service

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/prices", tags=["prices"])


@router.get("/indices")
@limiter.limit(settings.rate_limit_prices)
async def get_indices(
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    user_settings: Annotated[UserSettings, Depends(get_current_user_settings)],
    session: AsyncSession = Depends(get_session),
):
    """Return live quotes for major Indian indices (Nifty 50, Sensex, etc.)."""
    adapter = await get_adapter_for_user(
        str(user.id), user_settings.preferred_broker, session
    )
    quotes = await price_service.get_indices(adapter)
    broker_configured = adapter.is_credentials_configured()

    return {
        "broker":         adapter.broker_name,
        "is_configured":  broker_configured,
        "source":         "live" if broker_configured else "backfill",
        "indices":        [q.__dict__ for q in quotes],
        "warning":        None if broker_configured else (
            f"{user_settings.preferred_broker or 'Broker'} credentials not configured. "
            "Showing backfill data — prices may be delayed."
            if user_settings.preferred_broker
            else "No broker configured. Showing backfill data."
        ),
    }


@router.get("/{symbol}/quote")
@limiter.limit(settings.rate_limit_prices)
async def get_quote(
    request: Request,
    symbol: str,
    user: Annotated[User, Depends(get_current_user)],
    user_settings: Annotated[UserSettings, Depends(get_current_user_settings)],
    session: AsyncSession = Depends(get_session),
):
    """Get latest quote for a single symbol (e.g. RELIANCE, RELIANCE.NS)."""
    adapter = await get_adapter_for_user(
        str(user.id), user_settings.preferred_broker, session
    )
    quote = await price_service.get_quote(adapter, symbol.upper())
    if quote is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No data found for {symbol}")
    return {
        "broker":        adapter.broker_name,
        "is_configured": adapter.is_credentials_configured(),
        "source":        "live" if adapter.is_credentials_configured() else "backfill",
        "no_live_data":  adapter.broker_name == "yfinance",
        "quote":         quote.__dict__,
    }


@router.get("/{symbol}/history")
@limiter.limit(settings.rate_limit_prices)
async def get_history(
    request: Request,
    symbol: str,
    user: Annotated[User, Depends(get_current_user)],
    user_settings: Annotated[UserSettings, Depends(get_current_user_settings)],
    session: AsyncSession = Depends(get_session),
    period: str = Query("1y", description="1d, 5d, 1mo, 3mo, 6mo, 1y, 2y, 5y"),
    interval: str = Query("1d", description="1m, 5m, 15m, 1h, 1d"),
):
    """Fetch historical OHLCV bars for a symbol (for charting)."""
    adapter = await get_adapter_for_user(
        str(user.id), user_settings.preferred_broker, session
    )
    bars = await price_service.get_history(adapter, symbol.upper(), period, interval)
    return {
        "broker":   adapter.broker_name,
        "symbol":   symbol.upper(),
        "period":   period,
        "interval": interval,
        "bars":     [b.__dict__ for b in bars],
    }
