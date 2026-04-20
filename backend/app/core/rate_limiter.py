"""Rate limiting via slowapi.

Usage in endpoints:
    from app.core.rate_limiter import limiter

    @router.get("/foo")
    @limiter.limit("10/minute")
    async def foo(request: Request):
        ...

Attach to the app in main.py:
    from app.core.rate_limiter import limiter
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
"""
from __future__ import annotations

from slowapi import Limiter
from slowapi.util import get_remote_address

from app.core.config import settings

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[settings.rate_limit_default],
    storage_uri=settings.redis_url,
)
