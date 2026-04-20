"""BrokerCredential SQLModel � maps to the `broker_credentials` table.
All sensitive fields (api_key, api_secret, client_id, totp_secret) are stored
Fernet-encrypted using the same FERNET_KEY used for TOTP secrets.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


class BrokerCredential(SQLModel, table=True):
    __tablename__ = "broker_credentials"

    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        primary_key=True,
        nullable=False,
    )
    user_id: uuid.UUID = Field(foreign_key="users.id", index=True)
    broker_name: str = Field(max_length=20)   # 'angel_one' | 'upstox' | 'yfinance'

    # Fernet-encrypted fields � nullable until user provides them
    client_id: Optional[str] = Field(default=None)
    api_key: Optional[str] = Field(default=None)
    api_secret: Optional[str] = Field(default=None)
    totp_secret: Optional[str] = Field(default=None)  # Angel One only

    is_configured: bool = Field(default=False)
    pool_eligible: bool = Field(default=False)
    last_verified: Optional[datetime] = Field(default=None)

    # Upstox OAuth2 tokens (Fernet-encrypted)
    refresh_token: Optional[str] = Field(default=None)           # long-lived
    access_token: Optional[str] = Field(default=None)            # short-lived bearer
    access_token_expires_at: Optional[datetime] = Field(default=None)

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )
    tbl_last_dt: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )
