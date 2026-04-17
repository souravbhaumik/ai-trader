"""User SQLModel — maps to the `users` table managed by Alembic."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        primary_key=True,
        nullable=False,
    )
    email: str = Field(max_length=255, unique=True, index=True)
    hashed_password: str = Field(max_length=255)
    full_name: Optional[str] = Field(default=None, max_length=100)
    role: str = Field(default="trader", max_length=20)

    is_active: bool = Field(default=True)
    is_email_verified: bool = Field(default=False)
    is_live_trading_enabled: bool = Field(default=False)

    # TOTP — secret stored Fernet-encrypted; null if not configured
    totp_secret: Optional[str] = Field(default=None, max_length=255)
    is_totp_configured: bool = Field(default=False)

    invited_by: Optional[uuid.UUID] = Field(
        default=None, foreign_key="users.id", nullable=True
    )
    last_login_at: Optional[datetime] = Field(default=None)

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )
    tbl_last_dt: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )
