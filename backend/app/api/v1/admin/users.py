"""Admin-only user management: invite endpoint."""
from __future__ import annotations

import uuid
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.core.database import get_session
from app.core.security import decode_access_token
from app.models.user import User
from app.schemas.invite import InviteRequest, InviteResponse
from app.services.invite_service import InviteService

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


async def _require_admin(request: Request, session: AsyncSession) -> User:
    """Extract Bearer token and verify caller is an active admin."""
    auth_hdr = request.headers.get("Authorization", "")
    if not auth_hdr.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token.")
    token = auth_hdr.removeprefix("Bearer ").strip()
    try:
        payload = decode_access_token(token)
    except JWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token.")

    user_id = uuid.UUID(payload["sub"])
    role = payload.get("role", "")
    if role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin role required.")

    result = await session.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account inactive.")
    return user


@router.post("/users/invite", response_model=InviteResponse, status_code=status.HTTP_201_CREATED)
async def invite_user(
    body: InviteRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Create a single-use 24-hour invite for a new user (admin only)."""
    admin = await _require_admin(request, session)
    svc = InviteService(session)
    invite, raw_token = await svc.create_invite(
        email=body.email, invited_by=admin.id
    )
    from app.core.config import settings
    registration_url = f"{settings.frontend_url}/register?token={raw_token}"

    return InviteResponse(
        id=invite.id,
        email=invite.email,
        status=invite.status,
        expires_at=invite.expires_at,
        registration_url=registration_url,
        invite_token=raw_token,
    )
