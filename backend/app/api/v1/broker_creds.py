"""Broker Credentials API — save and retrieve encrypted broker API credentials."""
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


class BrokerCredsOut(BaseModel):
    broker_name: str
    is_configured: bool
    # masked values — show last 4 chars only
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
        return BrokerCredsOut(broker_name=broker_name, is_configured=False)

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

    # Mark configured if at least api_key + client_id are present
    creds.is_configured = bool(creds.api_key and creds.client_id)
    creds.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)

    session.add(creds)
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
