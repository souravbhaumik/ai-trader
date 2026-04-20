"""Invite-related request/response schemas."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr


class InviteRequest(BaseModel):
    email: EmailStr


class InviteResponse(BaseModel):
    id: uuid.UUID
    email: str
    status: str
    expires_at: datetime
    registration_url: str
    # Raw token — shown once to admin, never stored plain
    invite_token: str


class InviteListItem(BaseModel):
    id: uuid.UUID
    email: str
    status: str
    expires_at: datetime
    used_at: Optional[datetime]
    revoked_at: Optional[datetime]
    created_at: datetime


class InviteRevokeResponse(BaseModel):
    id: uuid.UUID
    email: str
    status: str
    revoked_at: Optional[datetime]
