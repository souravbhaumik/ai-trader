"""Auth endpoints: login, refresh, logout, register, TOTP setup."""
from __future__ import annotations

import base64
import io
import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from jose import JWTError
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_session
from app.core.security import (
    decode_access_token,
    decrypt_totp_secret,
    encrypt_totp_secret,
    generate_totp_secret,
    get_totp_uri,
    verify_totp,
)
from app.models.user import User
from app.schemas.auth import LoginRequest, MessageResponse, RegisterRequest, TokenResponse
from app.services.auth_service import AuthService
from app.core.security import hash_invite_token
from app.models.user_invites import UserInvite

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

_REFRESH_COOKIE = "refresh_token"
_COOKIE_MAX_AGE = 60 * 60 * 24 * 7  # 7 days in seconds
_IS_DEV = settings.environment == "development"
# Secure flag: True in production (HTTPS only), False in development
_SECURE_COOKIE = not _IS_DEV
# SameSite=lax allows the cookie to be sent on same-site POST requests
# from a different port (e.g. Vite :3002 → API :8000) in local development.
# In production use "strict" (same origin behind a reverse proxy).
_SAMESITE = "lax" if _IS_DEV else "strict"


def _set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=_REFRESH_COOKIE,
        value=token,
        httponly=True,
        samesite=_SAMESITE,
        secure=_SECURE_COOKIE,
        max_age=_COOKIE_MAX_AGE,
        path="/api/v1/auth",
    )


def _clear_refresh_cookie(response: Response) -> None:
    response.delete_cookie(
        key=_REFRESH_COOKIE,
        path="/api/v1/auth",
        httponly=True,
        samesite=_SAMESITE,
        secure=_SECURE_COOKIE,
    )


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    svc = AuthService(session)
    user, access_token, refresh_raw = await svc.login(
        email=body.email,
        password=body.password,
        totp_code=body.totp_code,
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )
    _set_refresh_cookie(response, refresh_raw)
    from app.schemas.auth import UserOut
    return TokenResponse(access_token=access_token, user=UserOut.model_validate(user))


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    raw = request.cookies.get(_REFRESH_COOKIE)
    if not raw:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing refresh token."
        )
    svc = AuthService(session)
    user, access_token = await svc.refresh(raw_refresh_token=raw)
    from app.schemas.auth import UserOut
    return TokenResponse(access_token=access_token, user=UserOut.model_validate(user))


@router.post("/logout", response_model=MessageResponse)
async def logout(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    raw = request.cookies.get(_REFRESH_COOKIE)
    if raw:
        svc = AuthService(session)
        await svc.logout(raw_refresh_token=raw)
    _clear_refresh_cookie(response)
    return MessageResponse(message="Logged out successfully.")


class _InviteStatusResponse(BaseModel):
    valid: bool
    email: str | None = None
    reason: str | None = None  # "revoked" | "expired" | "used" | "not_found"


@router.get("/invite-check", response_model=_InviteStatusResponse)
async def check_invite(
    token: str,
    session: AsyncSession = Depends(get_session),
):
    """Public endpoint — check if an invite token is still usable (no auth required)."""
    from app.services.auth_service import _utcnow, _strip_tz  # local import to avoid circular
    token_hash = hash_invite_token(token)
    result = await session.execute(
        select(UserInvite).where(UserInvite.token_hash == token_hash)
    )
    invite = result.scalar_one_or_none()
    if invite is None:
        return _InviteStatusResponse(valid=False, reason="not_found")
    if invite.status == "revoked":
        return _InviteStatusResponse(valid=False, reason="revoked")
    if invite.status == "used":
        return _InviteStatusResponse(valid=False, reason="used")
    if _strip_tz(invite.expires_at) < _utcnow():
        return _InviteStatusResponse(valid=False, reason="expired")
    return _InviteStatusResponse(valid=True, email=invite.email)


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    svc = AuthService(session)
    user, access_token, refresh_raw = await svc.register(
        invite_token=body.invite_token,
        full_name=body.full_name,
        password=body.password,
        user_agent=request.headers.get("user-agent"),
        ip_address=request.client.host if request.client else None,
    )
    _set_refresh_cookie(response, refresh_raw)
    from app.schemas.auth import UserOut
    return TokenResponse(
        access_token=access_token,
        user=UserOut.model_validate(user),
    )


# ══════════════════════════════════════════════════════════════════════════════
#  TOTP setup / enable / disable
# ══════════════════════════════════════════════════════════════════════════════

class _TotpSetupResponse(BaseModel):
    otpauth_uri: str
    qr_code_b64: str   # PNG encoded as base64 for display in the frontend


class _TotpVerifyRequest(BaseModel):
    code: str          # 6-digit TOTP code


async def _current_user(request: Request, session: AsyncSession) -> User:
    """Extract Bearer JWT, verify blocklist, and return the active User."""
    auth_hdr = request.headers.get("Authorization", "")
    if not auth_hdr.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token.")
    token = auth_hdr.removeprefix("Bearer ").strip()
    try:
        payload = decode_access_token(token)
    except JWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token.")

    # ── Blocklist check ───────────────────────────────────────────────────────
    from app.core.redis_client import get_redis as _get_redis
    jti = payload.get("jti")
    if jti and await _get_redis().exists(f"blocklist:{jti}"):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Token has been revoked.")

    user_id = uuid.UUID(payload["sub"])
    result  = await session.execute(select(User).where(User.id == user_id))
    user    = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Account inactive.")
    return user


@router.post("/totp/setup", response_model=_TotpSetupResponse)
async def totp_setup(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Generate a new TOTP secret and return the provisioning URI + QR code.

    The secret is stored encrypted in the user record but **not yet active**
    (``is_totp_configured`` remains False until the user calls ``/totp/enable``).
    """
    user = await _current_user(request, session)

    secret       = generate_totp_secret()
    otpauth_uri  = get_totp_uri(secret, user.email)

    # Generate QR code PNG (best-effort; requires qrcode package)
    qr_b64 = ""
    try:
        import qrcode  # type: ignore
        img     = qrcode.make(otpauth_uri)
        buf     = io.BytesIO()
        img.save(buf, format="PNG")
        qr_b64  = base64.b64encode(buf.getvalue()).decode()
    except ImportError:
        pass  # if qrcode is not installed the frontend can use the URI directly

    # Persist the encrypted secret (not yet enabled)
    user.totp_secret       = encrypt_totp_secret(secret)
    user.is_totp_configured = False
    session.add(user)
    await session.commit()

    return _TotpSetupResponse(otpauth_uri=otpauth_uri, qr_code_b64=qr_b64)


@router.post("/totp/enable", response_model=MessageResponse)
async def totp_enable(
    body: _TotpVerifyRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Verify a TOTP code and mark TOTP as active for this user."""
    user = await _current_user(request, session)

    if not user.totp_secret:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "No TOTP secret found. Call /auth/totp/setup first.",
        )
    if user.is_totp_configured:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "TOTP is already enabled.")

    secret = decrypt_totp_secret(user.totp_secret)
    if not verify_totp(secret, body.code.strip()):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid TOTP code.")

    user.is_totp_configured = True
    session.add(user)
    await session.commit()

    return MessageResponse(message="TOTP enabled successfully.")


@router.post("/totp/disable", response_model=MessageResponse)
async def totp_disable(
    body: _TotpVerifyRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Disable TOTP for the authenticated user after verifying the current code.

    The user must supply a valid code to prevent account lockout from a stolen
    session token.  Admins may bypass this via the admin/users API.
    """
    user = await _current_user(request, session)

    if not user.is_totp_configured or not user.totp_secret:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "TOTP is not configured.")

    secret = decrypt_totp_secret(user.totp_secret)
    if not verify_totp(secret, body.code.strip()):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid TOTP code.")

    user.totp_secret        = None
    user.is_totp_configured = False
    session.add(user)
    await session.commit()

    return MessageResponse(message="TOTP disabled.")


class _DeleteAccountRequest(BaseModel):
    password: str  # user must confirm with their password


@router.delete("/me", response_model=MessageResponse)
async def delete_own_account(
    body: _DeleteAccountRequest,
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
):
    """Permanently delete the caller's own account after password confirmation."""
    from app.core.security import verify_password
    user = await _current_user(request, session)
    if not verify_password(body.password, user.hashed_password):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Incorrect password.")
    if user.role == "admin":
        # Count other admins — must have at least one remaining
        result = await session.execute(
            select(User).where(User.role == "admin", User.id != user.id, User.is_active == True)  # noqa: E712
        )
        if not result.scalar_one_or_none():
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                "You are the only admin. Promote another user to admin before deleting your account."
            )
    await session.delete(user)
    await session.commit()
    _clear_refresh_cookie(response)
    return MessageResponse(message="Account deleted.")

