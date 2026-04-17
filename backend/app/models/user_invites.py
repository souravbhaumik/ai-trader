"""UserInvite SQLModel — maps to the `user_invites` table."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


class UserInvite(SQLModel, table=True):
    __tablename__ = "user_invites"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    email: str = Field(max_length=255, index=True)
    token_hash: str = Field(max_length=255, unique=True)
    # status: pending | used | expired | revoked
    status: str = Field(default="pending", max_length=20)

    invited_by: uuid.UUID = Field(foreign_key="users.id")
    user_id: Optional[uuid.UUID] = Field(
        default=None, foreign_key="users.id", nullable=True
    )

    expires_at: datetime
    used_at: Optional[datetime] = Field(default=None)
    revoked_at: Optional[datetime] = Field(default=None)

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )
    tbl_last_dt: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )
