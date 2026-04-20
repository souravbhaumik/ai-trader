"""Async Redis client (singleton connection pool)."""
from __future__ import annotations

import json

import redis.asyncio as aioredis

from app.core.config import settings

# Module-level pool — initialised in app lifespan
_redis: aioredis.Redis | None = None
_BROKER_SESSION_TTL = 23 * 60 * 60


def get_redis() -> aioredis.Redis:
    if _redis is None:
        raise RuntimeError("Redis not initialised. Call init_redis() first.")
    return _redis


async def init_redis() -> None:
    global _redis
    _redis = aioredis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=5,
        socket_timeout=5,
    )
    await _redis.ping()


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
        _redis = None


def _broker_session_key(user_id: str, broker_name: str) -> str:
    return f"broker:session:{user_id}:{broker_name}"


async def cache_broker_session(
    user_id: str,
    broker_name: str,
    jwt_token: str,
    feed_token: str | None = None,
    ttl_seconds: int = _BROKER_SESSION_TTL,
) -> None:
    """Persist broker session in Redis for cross-worker reuse."""
    payload = {
        "jwt_token": jwt_token,
        "feed_token": feed_token,
    }
    await get_redis().setex(
        _broker_session_key(user_id, broker_name),
        ttl_seconds,
        json.dumps(payload),
    )


async def get_cached_broker_session(user_id: str, broker_name: str) -> dict | None:
    """Read cached broker session payload from Redis."""
    raw = await get_redis().get(_broker_session_key(user_id, broker_name))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None
