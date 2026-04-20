"""Broker Credentials API � save and retrieve encrypted broker API credentials."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Annotated, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.api.v1.deps import get_current_user
from app.core.database import get_session
from app.core.security import decrypt_field, encrypt_field
from app.models.broker_credential import BrokerCredential
from app.models.user import User

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/broker-credentials", tags=["broker-credentials"])

_VALID_BROKERS = {"angel_one", "upstox"}


class BrokerCredsIn(BaseModel):
    client_id: Optional[str] = None
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    totp_secret: Optional[str] = None   # Angel One only
    pool_eligible: Optional[bool] = None


class BrokerCredsOut(BaseModel):
    broker_name: str
    is_configured: bool
    pool_eligible: bool = False
    # masked values � show last 4 chars only
    client_id_hint: Optional[str] = None
    api_key_hint: Optional[str] = None


@router.get("/{broker_name}", response_model=BrokerCredsOut)
async def get_broker_credentials(
    broker_name: str,
    user: Annotated[User, Depends(get_current_user)],
    session: AsyncSession = Depends(get_session),
):
    if broker_name not in _VALID_BROKERS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown broker: {broker_name}")

    result = await session.execute(
        select(BrokerCredential).where(
            BrokerCredential.user_id == user.id,
            BrokerCredential.broker_name == broker_name,
        )
    )
    creds = result.scalar_one_or_none()

    if not creds or not creds.is_configured:
        return BrokerCredsOut(
            broker_name=broker_name,
            is_configured=False,
            pool_eligible=bool(creds.pool_eligible) if creds else False,
        )

    def _mask(encrypted: Optional[str]) -> Optional[str]:
        if not encrypted:
            return None
        try:
            plain = decrypt_field(encrypted)
            return f"****{plain[-4:]}" if len(plain) > 4 else "****"
        except Exception:
            return None

    return BrokerCredsOut(
        broker_name=broker_name,
        is_configured=creds.is_configured,
        pool_eligible=bool(creds.pool_eligible),
        client_id_hint=_mask(creds.client_id),
        api_key_hint=_mask(creds.api_key),
    )


@router.put("/{broker_name}", response_model=BrokerCredsOut)
async def save_broker_credentials(
    broker_name: str,
    body: BrokerCredsIn,
    user: Annotated[User, Depends(get_current_user)],
    session: AsyncSession = Depends(get_session),
):
    if broker_name not in _VALID_BROKERS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown broker: {broker_name}")

    result = await session.execute(
        select(BrokerCredential).where(
            BrokerCredential.user_id == user.id,
            BrokerCredential.broker_name == broker_name,
        )
    )
    creds = result.scalar_one_or_none()

    if creds is None:
        creds = BrokerCredential(
            id=uuid.uuid4(),
            user_id=user.id,
            broker_name=broker_name,
        )

    if body.client_id is not None:
        creds.client_id = encrypt_field(body.client_id) if body.client_id else None
    if body.api_key is not None:
        creds.api_key = encrypt_field(body.api_key) if body.api_key else None
    if body.api_secret is not None:
        creds.api_secret = encrypt_field(body.api_secret) if body.api_secret else None
    if body.totp_secret is not None:
        creds.totp_secret = encrypt_field(body.totp_secret) if body.totp_secret else None
    if body.pool_eligible is not None:
        creds.pool_eligible = bool(body.pool_eligible)

    # Mark configured if at least api_key + client_id are present
    creds.is_configured = bool(creds.api_key and creds.client_id)
    creds.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)

    session.add(creds)

    # Auto-set preferred_broker when credentials are saved so the screener
    # uses this broker immediately without a separate Settings save step.
    if creds.is_configured:
        from app.models.user_settings import UserSettings
        from sqlmodel import select as _select
        settings_result = await session.execute(
            _select(UserSettings).where(UserSettings.user_id == user.id)
        )
        user_settings = settings_result.scalar_one_or_none()
        if user_settings and not user_settings.preferred_broker:
            user_settings.preferred_broker = broker_name
            session.add(user_settings)

    await session.commit()
    await session.refresh(creds)

    logger.info("broker_creds_saved", user_id=str(user.id), broker=broker_name,
                is_configured=creds.is_configured)

    def _mask(encrypted: Optional[str]) -> Optional[str]:
        if not encrypted:
            return None
        try:
            plain = decrypt_field(encrypted)
            return f"****{plain[-4:]}" if len(plain) > 4 else "****"
        except Exception:
            return None

    return BrokerCredsOut(
        broker_name=broker_name,
        is_configured=creds.is_configured,
        pool_eligible=bool(creds.pool_eligible),
        client_id_hint=_mask(creds.client_id),
        api_key_hint=_mask(creds.api_key),
    )


@router.delete("/{broker_name}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_broker_credentials(
    broker_name: str,
    user: Annotated[User, Depends(get_current_user)],
    session: AsyncSession = Depends(get_session),
):
    if broker_name not in _VALID_BROKERS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown broker: {broker_name}")

    result = await session.execute(
        select(BrokerCredential).where(
            BrokerCredential.user_id == user.id,
            BrokerCredential.broker_name == broker_name,
        )
    )
    creds = result.scalar_one_or_none()
    if creds:
        await session.delete(creds)
        await session.commit()


# ── Upstox OAuth2 endpoints ───────────────────────────────────────────────────

@router.get("/upstox/authorize")
async def upstox_authorize_url(
    user: Annotated[User, Depends(get_current_user)],
    session: AsyncSession = Depends(get_session),
):
    """Return the Upstox authorization URL the user must visit once.

    The user clicks this link, logs in to Upstox, and is redirected
    to /broker-credentials/upstox/callback with a one-time code.
    """
    from app.core.config import settings as app_settings
    from app.brokers.upstox import UpstoxAdapter

    result = await session.execute(
        select(BrokerCredential).where(
            BrokerCredential.user_id == user.id,
            BrokerCredential.broker_name == "upstox",
        )
    )
    creds = result.scalar_one_or_none()
    if not creds or not creds.api_key:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Save your Upstox API key first via PUT /broker-credentials/upstox",
        )

    api_key = decrypt_field(creds.api_key)
    redirect_uri = app_settings.upstox_redirect_uri or "http://localhost:8000/api/v1/broker-credentials/upstox/callback"
    adapter = UpstoxAdapter(api_key=api_key, redirect_uri=redirect_uri)
    auth_url = adapter.get_authorization_url()
    return {"authorization_url": auth_url}


@router.get("/upstox/callback")
async def upstox_oauth_callback(
    code: str,
    session: AsyncSession = Depends(get_session),
):
    """Upstox OAuth2 callback — exchanges the authorization code for tokens.

    Upstox redirects the user's browser here after login. This endpoint
    exchanges the code for an access_token, stores it encrypted, and
    returns a success page the user can close.

    Note: This endpoint is intentionally unauthenticated (no JWT required)
    because Upstox redirects the browser directly here — there is no
    Authorization header at this point. Security is provided by the
    one-time-use nature of the authorization code.
    """
    from app.core.config import settings as app_settings
    from app.core.security import encrypt_field
    from app.brokers.upstox import UpstoxAdapter
    from datetime import datetime, timezone, timedelta

    # Find the most recent unconfigured Upstox credentials row
    # (we match by api_key presence — the code belongs to our registered app)
    result = await session.execute(
        select(BrokerCredential).where(
            BrokerCredential.broker_name == "upstox",
            BrokerCredential.api_key.isnot(None),
        ).order_by(BrokerCredential.updated_at.desc())
    )
    creds = result.scalar_one_or_none()
    if not creds:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "No Upstox credentials found. Save your API key first.",
        )

    api_key    = decrypt_field(creds.api_key)
    api_secret = decrypt_field(creds.api_secret) if creds.api_secret else None
    redirect_uri = app_settings.upstox_redirect_uri or "http://localhost:8000/api/v1/broker-credentials/upstox/callback"

    adapter = UpstoxAdapter(
        api_key=api_key,
        api_secret=api_secret,
        redirect_uri=redirect_uri,
    )

    try:
        tokens = await adapter.exchange_code(code)
    except RuntimeError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    # Store encrypted tokens
    creds.access_token  = encrypt_field(tokens["access_token"])
    creds.refresh_token = encrypt_field(tokens["refresh_token"]) if tokens.get("refresh_token") else None
    # Upstox access tokens expire at midnight IST (UTC+5:30)
    now_ist = datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)
    midnight_ist = now_ist.replace(hour=23, minute=59, second=0, microsecond=0)
    creds.access_token_expires_at = midnight_ist.astimezone(timezone.utc).replace(tzinfo=None)
    creds.is_configured = True
    creds.last_verified = datetime.now(timezone.utc).replace(tzinfo=None)
    creds.updated_at    = datetime.now(timezone.utc).replace(tzinfo=None)

    session.add(creds)
    await session.commit()

    logger.info("upstox_oauth_complete", user_id=str(creds.user_id))

    # Return an HTML page the user can close
    from fastapi.responses import HTMLResponse
    return HTMLResponse(content="""
        <html><body style="font-family:sans-serif;text-align:center;padding:60px">
        <h2 style="color:#2ECC71">✓ Upstox Connected Successfully</h2>
        <p>You can close this tab and return to AI Trader.</p>
        <script>setTimeout(()=>window.close(),3000)</script>
        </body></html>
    """)
