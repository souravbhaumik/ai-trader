"""Seed the first admin user.

Run once after alembic upgrade head:
    python -m scripts.seed_admin_user

Reads ADMIN_EMAIL, ADMIN_PASSWORD, ADMIN_FULL_NAME from environment (or .env).
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from datetime import datetime, timezone

# Allow running as: python scripts/seed_admin_user.py from backend/
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select

from app.core.config import settings
from app.core.security import hash_password
from app.models.user import User
from app.models.user_settings import UserSettings


async def seed() -> None:
    email = os.environ.get("ADMIN_EMAIL", "admin@example.com")
    password = os.environ.get("ADMIN_PASSWORD", "")
    full_name = os.environ.get("ADMIN_FULL_NAME", "Admin")

    if not password:
        print("ERROR: ADMIN_PASSWORD env var is required.", file=sys.stderr)
        sys.exit(1)

    engine = create_async_engine(settings.database_url, echo=False)
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with async_session() as session:
        result = await session.execute(select(User).where(User.email == email))
        existing = result.scalar_one_or_none()
        if existing:
            print(f"Admin user '{email}' already exists. Skipping.")
            return

        user = User(
            email=email,
            hashed_password=hash_password(password),
            full_name=full_name,
            role="admin",
            is_active=True,
            is_email_verified=True,
        )
        session.add(user)
        await session.flush()

        user_settings = UserSettings(user_id=user.id)
        session.add(user_settings)

        await session.commit()
        print(f"Admin user created: {email} (id={user.id})")
        print("To enable TOTP, implement the TOTP setup endpoint in Phase 2.")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
