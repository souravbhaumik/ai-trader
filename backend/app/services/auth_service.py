"""Authentication service — login, refresh, logout, register."""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone


def _utcnow() -> datetime:
    """Naive UTC timestamp for writes (asyncpg casts to TIMESTAMP WITHOUT TIME ZONE)."""
    return datetime.utcnow()


def _strip_tz(dt: datetime) -> datetime:
    """Normalize a datetime from the DB (may be tz-aware) to naive UTC for comparison."""
    if dt.tzinfo is not None:
        return dt.replace(tzinfo=None)
    return dt

import structlog
from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.redis_client import get_redis
from app.core.security import (
    create_access_token,
    decrypt_totp_secret,
    generate_refresh_token,
    hash_invite_token,
    hash_token,
    hash_password,
    verify_password,
    verify_totp,
)
from app.models.refresh_tokens import RefreshToken
from app.models.user import User
from app.models.user_invites import UserInvite
from app.models.user_settings import UserSettings

logger = structlog.get_logger(__name__)

_BLOCKLIST_PREFIX = "blocklist:"


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Login ─────────────────────────────────────────────────────────────────

    async def login(
        self,
        *,
        email: str,
        password: str,
        totp_code: str | None,
        user_agent: str | None,
        ip_address: str | None,
    ) -> tuple[User, str, str]:
        """Return (user, access_token, raw_refresh_token)."""
        user = await self._get_active_user_by_email(email)

        if not verify_password(password, user.hashed_password):
            logger.warning("login.bad_password", email=email)
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials.")

        # TOTP required for admin who have configured it
        if user.role == "admin" and user.is_totp_configured:
            if not totp_code:
                raise HTTPException(
                    status.HTTP_401_UNAUTHORIZED,
                    "TOTP code required for admin accounts.",
                )
            plain_secret = decrypt_totp_secret(user.totp_secret)  # type: ignore[arg-type]
            if not verify_totp(plain_secret, totp_code):
                logger.warning("login.bad_totp", user_id=str(user.id))
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid TOTP code.")

        access_token, jti = create_access_token(user.id, user.role)
        refresh_raw, rt = await self._issue_refresh_token(
            user=user, jti=jti, user_agent=user_agent, ip_address=ip_address
        )

        # Update last_login_at
        await self._session.execute(
            update(User)
            .where(User.id == user.id)
            .values(last_login_at=_utcnow())
        )
        await self._session.commit()

        logger.info("login.success", user_id=str(user.id), role=user.role)
        return user, access_token, refresh_raw

    # ── Refresh ───────────────────────────────────────────────────────────────

    async def refresh(self, *, raw_refresh_token: str) -> tuple[User, str]:
        """Return (user, new_access_token). Refresh token is NOT rotated."""
        token_hash = hash_token(raw_refresh_token)
        rt = await self._get_valid_refresh_token(token_hash)

        user = await self._get_active_user_by_id(rt.user_id)

        # Check jti not blocklisted
        redis = get_redis()
        if await redis.exists(f"{_BLOCKLIST_PREFIX}{rt.jti}"):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token has been revoked.")

        access_token, _ = create_access_token(user.id, user.role)
        return user, access_token

    # ── Logout ────────────────────────────────────────────────────────────────

    async def logout(self, *, raw_refresh_token: str) -> None:
        """Blocklist the jti and mark refresh token as revoked."""
        token_hash = hash_token(raw_refresh_token)
        result = await self._session.execute(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )
        rt = result.scalar_one_or_none()
        if rt is None or rt.revoked_at is not None:
            return  # Already revoked or unknown — safe no-op

        now = _utcnow()
        remaining = _strip_tz(rt.expires_at) - now
        if remaining.total_seconds() > 0:
            redis = get_redis()
            await redis.setex(
                f"{_BLOCKLIST_PREFIX}{rt.jti}",
                int(remaining.total_seconds()),
                "1",
            )

        await self._session.execute(
            update(RefreshToken)
            .where(RefreshToken.id == rt.id)
            .values(revoked_at=now)
        )
        await self._session.commit()
        logger.info("logout.success", user_id=str(rt.user_id))

    # ── Register (with invite token) ──────────────────────────────────────────

    async def register(
        self,
        *,
        invite_token: str,
        full_name: str,
        password: str,
        user_agent: str | None,
        ip_address: str | None,
    ) -> tuple[User, str, str]:
        """Validate invite, create user + settings, return tokens."""
        token_hash = hash_invite_token(invite_token)
        result = await self._session.execute(
            select(UserInvite).where(UserInvite.token_hash == token_hash)
        )
        invite = result.scalar_one_or_none()

        if invite is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid invite token.")
        if invite.status != "pending":
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Invite is {invite.status} and cannot be used.",
            )
        if _strip_tz(invite.expires_at) < _utcnow():
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invite has expired.")

        # Check email not already registered
        existing = await self._session.execute(
            select(User).where(User.email == invite.email)
        )
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "An account with this email already exists."
            )

        user = User(
            email=invite.email,
            hashed_password=hash_password(password),
            full_name=full_name,
            role="trader",
            invited_by=invite.invited_by,
        )
        self._session.add(user)
        await self._session.flush()  # populate user.id

        settings_row = UserSettings(user_id=user.id)
        self._session.add(settings_row)

        now = _utcnow()
        invite.status = "used"
        invite.used_at = now
        invite.user_id = user.id
        self._session.add(invite)

        access_token, jti = create_access_token(user.id, user.role)
        refresh_raw, _ = await self._issue_refresh_token(
            user=user, jti=jti, user_agent=user_agent, ip_address=ip_address
        )

        await self._session.commit()
        logger.info("register.success", user_id=str(user.id), email=user.email)
        return user, access_token, refresh_raw

    # ── Private helpers ───────────────────────────────────────────────────────

    async def _get_active_user_by_email(self, email: str) -> User:
        result = await self._session.execute(
            select(User).where(User.email == email)
        )
        user = result.scalar_one_or_none()
        if user is None or not user.is_active:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials.")
        return user

    async def _get_active_user_by_id(self, user_id: uuid.UUID) -> User:
        result = await self._session.execute(
            select(User).where(User.id == user_id)
        )
        user = result.scalar_one_or_none()
        if user is None or not user.is_active:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "User not found or inactive.")
        return user

    async def _get_valid_refresh_token(self, token_hash: str) -> RefreshToken:
        result = await self._session.execute(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash)
        )
        rt = result.scalar_one_or_none()
        if rt is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token.")
        if rt.revoked_at is not None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Refresh token has been revoked.")
        if _strip_tz(rt.expires_at) < _utcnow():
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Refresh token has expired.")
        return rt

    async def _issue_refresh_token(
        self,
        *,
        user: User,
        jti: uuid.UUID,
        user_agent: str | None,
        ip_address: str | None,
    ) -> tuple[str, RefreshToken]:
        raw = generate_refresh_token()
        expires_at = _utcnow() + timedelta(
            days=settings.refresh_token_expire_days
        )
        rt = RefreshToken(
            user_id=user.id,
            token_hash=hash_token(raw),
            jti=jti,
            expires_at=expires_at,
            user_agent=user_agent,
            ip_address=ip_address,
        )
        self._session.add(rt)
        return raw, rt
