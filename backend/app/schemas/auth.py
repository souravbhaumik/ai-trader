"""Pydantic v2 schemas for auth endpoints."""
from __future__ import annotations

import uuid
from typing import Optional

from pydantic import BaseModel, EmailStr, field_validator


# ── Request schemas ────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    totp_code: Optional[str] = None  # required for admin if TOTP configured


class RegisterRequest(BaseModel):
    invite_token: str
    full_name: str
    password: str

    @field_validator("password")
    @classmethod
    def password_strength(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters.")
        has_upper = any(c.isupper() for c in v)
        has_digit = any(c.isdigit() for c in v)
        if not has_upper or not has_digit:
            raise ValueError(
                "Password must contain at least one uppercase letter and one digit."
            )
        return v


# ── Response schemas ───────────────────────────────────────────────────────────

class UserOut(BaseModel):
    id: uuid.UUID
    email: str
    full_name: Optional[str]
    role: str
    is_active: bool
    is_totp_configured: bool
    is_live_trading_enabled: bool

    model_config = {"from_attributes": True}


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class MessageResponse(BaseModel):
    message: str
