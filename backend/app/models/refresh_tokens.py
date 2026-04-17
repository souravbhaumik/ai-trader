"""RefreshToken SQLModel — maps to the `refresh_tokens` table."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


class RefreshToken(SQLModel, table=True):
    __tablename__ = "refresh_tokens"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="users.id", index=True)
    # SHA-256 of the raw opaque token sent in the cookie
    token_hash: str = Field(max_length=255, unique=True)
    # JWT `jti` claim issued together with this refresh token
    jti: uuid.UUID = Field(unique=True, index=True)

    issued_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )
    expires_at: datetime
    revoked_at: Optional[datetime] = Field(default=None)

    user_agent: Optional[str] = Field(default=None, max_length=255)
    ip_address: Optional[str] = Field(default=None, max_length=45)

    tbl_last_dt: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )
