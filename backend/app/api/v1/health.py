"""Health check endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.core.database import get_session
from app.core.redis_client import get_redis

router = APIRouter(tags=["health"])


@router.get("/health")
async def health(session: AsyncSession = Depends(get_session)):
    """Liveness + readiness probe. Checks DB and Redis connectivity."""
    # DB check
    await session.execute(text("SELECT 1"))

    # Redis check
    redis = get_redis()
    await redis.ping()

    return {"status": "ok", "db": "ok", "redis": "ok"}
