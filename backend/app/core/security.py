"""JWT, bcrypt, TOTP, and Fernet helpers."""
from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone

import pyotp
from cryptography.fernet import Fernet
from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings

# ── Password hashing (bcrypt, cost configurable via settings) ─────────────────
_pwd_context = CryptContext(
    schemes=["bcrypt"],
    deprecated="auto",
    bcrypt__rounds=settings.bcrypt_rounds,
)


def hash_password(plain: str) -> str:
    return _pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd_context.verify(plain, hashed)


# ── JWT access tokens ─────────────────────────────────────────────────────────

def create_access_token(user_id: uuid.UUID, role: str) -> tuple[str, uuid.UUID]:
    """Return (encoded_jwt, jti). jti is stored nowhere — only in the token."""
    jti = uuid.uuid4()
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.access_token_expire_minutes
    )
    payload = {
        "sub": str(user_id),
        "role": role,
        "jti": str(jti),
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    token = jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    return token, jti


def decode_access_token(token: str) -> dict:
    """Raises JWTError if invalid or expired."""
    return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])


# ── Refresh tokens (opaque 32-byte hex, SHA-256 stored in DB) ─────────────────

def generate_refresh_token() -> str:
    """Return a 64-char hex string (32 bytes)."""
    return secrets.token_hex(32)


def hash_token(raw: str) -> str:
    """SHA-256 the raw token hex for safe storage."""
    return hashlib.sha256(raw.encode()).hexdigest()


# ── Invite tokens (HMAC-SHA256, 24-h expiry enforced in DB) ──────────────────

def generate_invite_token() -> str:
    """Return a 64-char hex string used as the invite token."""
    return secrets.token_hex(32)


def hash_invite_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


# ── Fernet (TOTP secret encryption) ──────────────────────────────────────────

_fernet = Fernet(settings.fernet_key.encode())


def encrypt_totp_secret(plain: str) -> str:
    return _fernet.encrypt(plain.encode()).decode()


def decrypt_totp_secret(ciphertext: str) -> str:
    return _fernet.decrypt(ciphertext.encode()).decode()


# Generic field encryption used by broker_credentials
encrypt_field = encrypt_totp_secret
decrypt_field = decrypt_totp_secret


# ── TOTP ──────────────────────────────────────────────────────────────────────

def generate_totp_secret() -> str:
    return pyotp.random_base32()


def get_totp_uri(secret: str, email: str) -> str:
    return pyotp.totp.TOTP(secret).provisioning_uri(name=email, issuer_name="AI Trader")


def verify_totp(secret: str, code: str) -> bool:
    """Verify a 6-digit TOTP code. Allows 1-step clock drift."""
    totp = pyotp.TOTP(secret)
    return totp.verify(code, valid_window=1)
