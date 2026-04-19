"""Admin-only user management: invite + user CRUD endpoints."""
from __future__ import annotations

import uuid
import threading
from datetime import datetime
from typing import Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from jose import JWTError
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete as sa_delete

from app.core.database import get_session
from app.core.security import decode_access_token
from app.models.user import User
from app.schemas.invite import InviteRequest, InviteListItem, InviteResponse, InviteRevokeResponse
from app.services.invite_service import InviteService


class UserListItem(BaseModel):
    id: uuid.UUID
    email: str
    full_name: Optional[str]
    role: str
    is_active: bool
    is_totp_configured: bool
    last_login_at: Optional[datetime]
    created_at: datetime


class UserDeleteResponse(BaseModel):
    id: uuid.UUID
    email: str
    deleted: bool

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

    # Send invite email in a background thread — non-blocking, failure is logged not raised
    from app.services.email_service import send_invite_email
    threading.Thread(
        target=send_invite_email,
        kwargs={
            "to_email": body.email,
            "registration_url": registration_url,
            "invited_by_name": admin.full_name or "Admin",
        },
        daemon=True,
    ).start()

    return InviteResponse(
        id=invite.id,
        email=invite.email,
        status=invite.status,
        expires_at=invite.expires_at,
        registration_url=registration_url,
        invite_token=raw_token,
    )


@router.get("/users/invites", response_model=list[InviteListItem])
async def list_invites(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Return the 50 most recent invites (admin only)."""
    await _require_admin(request, session)
    svc = InviteService(session)
    invites = await svc.list_invites()
    return [
        InviteListItem(
            id=inv.id,
            email=inv.email,
            status=inv.status,
            expires_at=inv.expires_at,
            used_at=inv.used_at,
            revoked_at=inv.revoked_at,
            created_at=inv.created_at,
        )
        for inv in invites
    ]


@router.delete("/users/invites/{invite_id}", response_model=InviteRevokeResponse)
async def revoke_invite(
    invite_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Revoke a pending invite (admin only). Cannot revoke used/expired invites."""
    await _require_admin(request, session)
    svc = InviteService(session)
    invite = await svc.revoke_invite(invite_id)
    return InviteRevokeResponse(id=invite.id, email=invite.email, status=invite.status, revoked_at=invite.revoked_at)


@router.get("/users", response_model=list[UserListItem])
async def list_users(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Return all registered users (admin only)."""
    await _require_admin(request, session)
    result = await session.execute(select(User).order_by(User.created_at.desc()))
    users = result.scalars().all()
    return [
        UserListItem(
            id=u.id,
            email=u.email,
            full_name=u.full_name,
            role=u.role,
            is_active=u.is_active,
            is_totp_configured=u.is_totp_configured,
            last_login_at=u.last_login_at,
            created_at=u.created_at,
        )
        for u in users
    ]


@router.delete("/users/{user_id}", response_model=UserDeleteResponse)
async def delete_user(
    user_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Permanently delete a user account (admin only). Admins cannot delete themselves."""
    admin = await _require_admin(request, session)
    if admin.id == user_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "You cannot delete your own account from here. Use account settings instead.")

    result = await session.execute(select(User).where(User.id == user_id))
    target = result.scalar_one_or_none()
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found.")

    email = target.email
    await session.delete(target)
    await session.commit()
    logger.info("admin.user_deleted", deleted_user=str(user_id), deleted_email=email, by_admin=str(admin.id))
    return UserDeleteResponse(id=user_id, email=email, deleted=True)


# ── Global live-trading kill switch ───────────────────────────────────────────

class KillSwitchResponse(BaseModel):
    disabled_count: int
    detail: str


@router.delete(
    "/live-trading",
    response_model=KillSwitchResponse,
    summary="Kill switch — disable live trading for ALL users immediately",
)
async def kill_switch_live_trading(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Set ``is_live_trading_enabled = false`` for every user in the system.

    Also flips every user_settings row with ``trading_mode = 'live'`` back to
    ``'paper'`` so no pending order-placement tasks can slip through.

    This is an irreversible batch operation — users must individually re-enable
    live trading via the OTP flow.
    """
    admin = await _require_admin(request, session)

    from sqlalchemy import update as sa_update, func  # noqa: PLC0415
    from datetime import datetime, timezone  # noqa: PLC0415

    now_ts = datetime.now(timezone.utc).replace(tzinfo=None)

    # Disable is_live_trading_enabled on every user
    update_users = (
        sa_update(User)
        .where(User.is_live_trading_enabled == True)  # noqa: E712
        .values(is_live_trading_enabled=False, updated_at=now_ts)
        .returning(User.id)
    )
    result = await session.execute(update_users)
    disabled_count = len(result.fetchall())

    # Also flip trading_mode back to paper for all live-mode users
    from app.models.user_settings import UserSettings  # noqa: PLC0415
    await session.execute(
        sa_update(UserSettings)
        .where(UserSettings.trading_mode == "live")
        .values(trading_mode="paper", updated_at=now_ts)
    )

    await session.commit()

    logger.warning(
        "admin.kill_switch_activated",
        by_admin=str(admin.id),
        disabled_count=disabled_count,
    )

    return KillSwitchResponse(
        disabled_count=disabled_count,
        detail=(
            f"Live trading disabled for {disabled_count} user(s). "
            "All accounts have been switched to paper trading."
        ),
    )

