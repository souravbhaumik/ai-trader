"""Expo push token model for mobile notifications."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlmodel import Field, SQLModel


class ExpoPushToken(SQLModel, table=True):
    __tablename__ = "expo_push_tokens"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True, nullable=False)
    user_id: uuid.UUID = Field(foreign_key="users.id", index=True)
    token: str = Field(max_length=200)
    device_id: str = Field(max_length=100)
    platform: str = Field(max_length=10)  # ios | android
    is_active: bool = Field(default=True)
    registered_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    last_used_at: datetime | None = Field(default=None)
    tbl_last_dt: datetime = Field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
