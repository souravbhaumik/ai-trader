"""Shared FastAPI dependencies used across multiple API modules."""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.database import get_session
from app.core.security import decode_access_token
from app.models.user import User
from app.models.user_settings import UserSettings


async def get_current_user(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> User:
    """Extract Bearer token and return the authenticated User."""
    auth_hdr = request.headers.get("Authorization", "")
    if not auth_hdr.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token.")
    token = auth_hdr.removeprefix("Bearer ").strip()
    try:
        payload = decode_access_token(token)
    except JWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token.")

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
