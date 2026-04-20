"""Settings API � read and update user trading preferences."""
from __future__ import annotations

import random
import string
from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

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
    enforce_market_hours: bool
    max_sector_exposure_pct: float


class SettingsPatch(BaseModel):
    trading_mode: Optional[str] = None
    paper_balance: Optional[float] = None
    max_position_pct: Optional[float] = None
    daily_loss_limit_pct: Optional[float] = None
    notification_signals: Optional[bool] = None
    notification_orders: Optional[bool] = None
    notification_news: Optional[bool] = None
    preferred_broker: Optional[str] = None
    enforce_market_hours: Optional[bool] = None
    max_sector_exposure_pct: Optional[float] = None


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
        enforce_market_hours=user_settings.enforce_market_hours,
        max_sector_exposure_pct=float(user_settings.max_sector_exposure_pct),
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
        valid_brokers = {"angel_one", "upstox"}
        if body.preferred_broker not in valid_brokers:
            from fastapi import HTTPException, status
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                f"preferred_broker must be one of {sorted(valid_brokers)}")
        user_settings.preferred_broker = body.preferred_broker
    if body.enforce_market_hours is not None:
        user_settings.enforce_market_hours = body.enforce_market_hours
    if body.max_sector_exposure_pct is not None:
        if not (5.0 <= body.max_sector_exposure_pct <= 100.0):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                "max_sector_exposure_pct must be between 5 and 100")
        user_settings.max_sector_exposure_pct = Decimal(str(body.max_sector_exposure_pct))

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
        enforce_market_hours=user_settings.enforce_market_hours,
        max_sector_exposure_pct=float(user_settings.max_sector_exposure_pct),
    )


# -- Live-trading enablement (email OTP gate) ----------------------------------

_OTP_TTL    = 600     # seconds — code expires after 10 minutes (per API.md spec)
_OTP_DIGITS = 6
_OTP_MAX_ATTEMPTS = 5
_OTP_LOCKOUT_TTL  = 900   # 15-minute lockout after max attempts


def _otp_redis_key(user_id) -> str:
    return f"live_enable_otp:{user_id}"


def _otp_attempts_key(user_id) -> str:
    return f"live_enable_attempts:{user_id}"


class EnableLiveTradingResponse(BaseModel):
    detail: str


class ConfirmLiveTradingRequest(BaseModel):
    code: str


@router.post(
    "/live-trading/enable",
    response_model=EnableLiveTradingResponse,
    summary="Request a 6-digit OTP to enable live trading",
)
async def request_live_trading_otp(
    user: Annotated[User, Depends(get_current_user)],
):
    """Send a one-time passcode to the user's email address.

    The user must call ``POST /settings/live-trading/confirm`` with the code
    within 2 minutes to flip ``is_live_trading_enabled = true``.
    """
    if user.is_live_trading_enabled:
        return EnableLiveTradingResponse(detail="Live trading is already enabled.")

    code = "".join(random.choices(string.digits, k=_OTP_DIGITS))

    from app.core.redis_client import get_redis
    redis = get_redis()
    await redis.set(_otp_redis_key(user.id), code, ex=_OTP_TTL)

    # Send OTP via email (no-op if SMTP not configured)
    from app.services.email_service import send_live_trading_otp_email  # noqa: PLC0415
    send_live_trading_otp_email(to_email=user.email, otp_code=code)

    logger.info("live_trading.otp_sent", user_id=str(user.id))
    return EnableLiveTradingResponse(
        detail=f"A 6-digit code has been sent to {user.email}. It expires in 2 minutes."
    )


@router.post(
    "/live-trading/confirm",
    response_model=EnableLiveTradingResponse,
    summary="Confirm OTP and enable live trading",
)
async def confirm_live_trading_otp(
    body: ConfirmLiveTradingRequest,
    user: Annotated[User, Depends(get_current_user)],
    session: AsyncSession = Depends(get_session),
):
    """Verify the OTP and set ``is_live_trading_enabled = true``."""
    if user.is_live_trading_enabled:
        return EnableLiveTradingResponse(detail="Live trading is already enabled.")

    from app.core.redis_client import get_redis
    redis = get_redis()

    # ── Brute-force protection ────────────────────────────────────────────────
    attempts_key = _otp_attempts_key(user.id)
    attempts = await redis.get(attempts_key)
    attempts_int = int(attempts) if attempts else 0
    if attempts_int >= _OTP_MAX_ATTEMPTS:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            f"Too many failed attempts. Please wait 15 minutes and request a new OTP."
        )

    stored = await redis.get(_otp_redis_key(user.id))

    if stored is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "OTP expired or never requested. Call /settings/live-trading/enable first.")

    stored_str = stored.decode() if isinstance(stored, bytes) else stored
    if stored_str != body.code.strip():
        # Increment failed attempts
        pipe = redis.pipeline()
        pipe.incr(attempts_key)
        pipe.expire(attempts_key, _OTP_LOCKOUT_TTL)
        await pipe.execute()
        remaining = _OTP_MAX_ATTEMPTS - attempts_int - 1
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Invalid OTP code. {remaining} attempt(s) remaining."
        )

    # Verified — enable live trading and delete OTP + attempts
    await redis.delete(_otp_redis_key(user.id))
    await redis.delete(attempts_key)

    result = await session.execute(select(User).where(User.id == user.id))
    db_user = result.scalar_one()
    db_user.is_live_trading_enabled = True
    db_user.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    session.add(db_user)
    await session.commit()

    logger.info("live_trading.enabled", user_id=str(user.id))
    return EnableLiveTradingResponse(detail="Live trading has been enabled for your account.")

