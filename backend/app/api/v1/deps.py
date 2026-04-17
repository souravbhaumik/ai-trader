"""Shared FastAPI dependencies used across multiple API modules."""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from jose import JWTError
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.database import get_session
from app.core.redis_client import get_redis
from app.core.security import decode_access_token
from app.models.user import User
from app.models.user_settings import UserSettings

_BLOCKLIST_PREFIX = "blocklist:"


async def get_current_user(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> User:
    """Extract Bearer token and return the authenticated User.

    Checks the Redis JTI blocklist on every request so that logged-out or
    admin-revoked tokens are rejected immediately (within the 15-min TTL).
    """
    auth_hdr = request.headers.get("Authorization", "")
    if not auth_hdr.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token.")
    token = auth_hdr.removeprefix("Bearer ").strip()
    try:
        payload = decode_access_token(token)
    except JWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token.")

    # ── Blocklist check: reject tokens whose JTI was revoked on logout ────────
    jti = payload.get("jti")
    if jti:
        try:
            redis = get_redis()
            if await redis.exists(f"{_BLOCKLIST_PREFIX}{jti}"):
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token has been revoked.")
        except HTTPException:
            raise
        except RedisConnectionError:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Auth service degraded — please try again shortly.",
            )

    user_id = uuid.UUID(payload["sub"])
    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User inactive or not found.")
    return user


async def get_current_user_settings(
    user: Annotated[User, Depends(get_current_user)],
    session: AsyncSession = Depends(get_session),
) -> UserSettings:
    """Return UserSettings for the authenticated user (creates defaults if missing)."""
    result = await session.execute(
        select(UserSettings).where(UserSettings.user_id == user.id)
    )
    settings_row = result.scalar_one_or_none()
    if settings_row is None:
        settings_row = UserSettings(user_id=user.id)
        session.add(settings_row)
        await session.commit()
        await session.refresh(settings_row)
    return settings_row
