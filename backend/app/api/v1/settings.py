"""Settings API — read and update user trading preferences."""
from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Optional

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.deps import get_current_user, get_current_user_settings
from app.core.database import get_session
from app.models.user import User
from app.models.user_settings import UserSettings

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/settings", tags=["settings"])


class SettingsOut(BaseModel):
    trading_mode: str
    paper_balance: float
    max_position_pct: float
    daily_loss_limit_pct: float
    notification_signals: bool
    notification_orders: bool
    notification_news: bool
    preferred_broker: Optional[str]


class SettingsPatch(BaseModel):
    trading_mode: Optional[str] = None
    paper_balance: Optional[float] = None
    max_position_pct: Optional[float] = None
    daily_loss_limit_pct: Optional[float] = None
    notification_signals: Optional[bool] = None
    notification_orders: Optional[bool] = None
    notification_news: Optional[bool] = None
    preferred_broker: Optional[str] = None


@router.get("", response_model=SettingsOut)
async def get_settings(
    user: Annotated[User, Depends(get_current_user)],
    user_settings: Annotated[UserSettings, Depends(get_current_user_settings)],
):
    return SettingsOut(
        trading_mode=user_settings.trading_mode,
        paper_balance=float(user_settings.paper_balance),
        max_position_pct=float(user_settings.max_position_pct),
        daily_loss_limit_pct=float(user_settings.daily_loss_limit_pct),
        notification_signals=user_settings.notification_signals,
        notification_orders=user_settings.notification_orders,
        notification_news=user_settings.notification_news,
        preferred_broker=user_settings.preferred_broker,
    )


@router.patch("", response_model=SettingsOut)
async def update_settings(
    body: SettingsPatch,
    user: Annotated[User, Depends(get_current_user)],
    user_settings: Annotated[UserSettings, Depends(get_current_user_settings)],
    session: AsyncSession = Depends(get_session),
):
    if body.trading_mode is not None:
        if body.trading_mode not in ("paper", "live"):
            from fastapi import HTTPException, status
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                "trading_mode must be 'paper' or 'live'")
        user_settings.trading_mode = body.trading_mode
    if body.paper_balance is not None:
        user_settings.paper_balance = Decimal(str(body.paper_balance))
    if body.max_position_pct is not None:
        user_settings.max_position_pct = Decimal(str(body.max_position_pct))
    if body.daily_loss_limit_pct is not None:
        user_settings.daily_loss_limit_pct = Decimal(str(body.daily_loss_limit_pct))
    if body.notification_signals is not None:
        user_settings.notification_signals = body.notification_signals
    if body.notification_orders is not None:
        user_settings.notification_orders = body.notification_orders
    if body.notification_news is not None:
        user_settings.notification_news = body.notification_news
    if body.preferred_broker is not None:
        valid_brokers = {"yfinance", "angel_one", "upstox"}
        if body.preferred_broker not in valid_brokers:
            from fastapi import HTTPException, status
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                f"preferred_broker must be one of {sorted(valid_brokers)}")
        user_settings.preferred_broker = body.preferred_broker

    from datetime import datetime, timezone
    user_settings.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)

    session.add(user_settings)
    await session.commit()
    await session.refresh(user_settings)

    return SettingsOut(
        trading_mode=user_settings.trading_mode,
        paper_balance=float(user_settings.paper_balance),
        max_position_pct=float(user_settings.max_position_pct),
        daily_loss_limit_pct=float(user_settings.daily_loss_limit_pct),
        notification_signals=user_settings.notification_signals,
        notification_orders=user_settings.notification_orders,
        notification_news=user_settings.notification_news,
        preferred_broker=user_settings.preferred_broker,
    )
