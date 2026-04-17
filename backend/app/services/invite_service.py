"""Invite token service."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone


def _utcnow() -> datetime:
    """Naive UTC timestamp for writes (asyncpg casts to TIMESTAMP WITHOUT TIME ZONE)."""
    return datetime.utcnow()

import structlog
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import generate_invite_token, hash_invite_token
from app.models.user_invites import UserInvite

logger = structlog.get_logger(__name__)

_INVITE_TTL_HOURS = 24


class InviteService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_invite(
        self, *, email: str, invited_by: uuid.UUID
    ) -> tuple[UserInvite, str]:
        """Generate a single-use invite. Returns (UserInvite, raw_token)."""
        # Revoke any prior pending invite for this email
        result = await self._session.execute(
            select(UserInvite).where(
                UserInvite.email == email,
                UserInvite.status == "pending",
            )
        )
        prior = result.scalar_one_or_none()
        if prior is not None:
            prior.status = "revoked"
            prior.revoked_at = _utcnow()
            self._session.add(prior)

        raw_token = generate_invite_token()
        invite = UserInvite(
            email=email,
            token_hash=hash_invite_token(raw_token),
            invited_by=invited_by,
            expires_at=_utcnow() + timedelta(hours=_INVITE_TTL_HOURS),
        )
        self._session.add(invite)
        await self._session.commit()
        await self._session.refresh(invite)

        logger.info("invite.created", email=email, invited_by=str(invited_by))
        return invite, raw_token

    async def list_invites(self, limit: int = 50) -> list[UserInvite]:
        """Return the most recent invites, newest first."""
        result = await self._session.execute(
            select(UserInvite).order_by(UserInvite.created_at.desc()).limit(limit)
        )
        return list(result.scalars().all())
