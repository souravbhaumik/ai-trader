"""Request/response logging middleware with sensitive-data redaction."""
from __future__ import annotations

import time
import re

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = structlog.get_logger(__name__)

# Strip ?token=... from logged paths
_TOKEN_RE = re.compile(r"([\?&]token=)[^&]*", re.IGNORECASE)


def _sanitize_path(path: str, query: str) -> str:
    full = f"{path}?{query}" if query else path
    return _TOKEN_RE.sub(r"\1[REDACTED]", full)


class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        sanitized = _sanitize_path(request.url.path, request.url.query)
        start = time.perf_counter()

        try:
            response = await call_next(request)
        except Exception as exc:
            logger.exception(
                "request.error",
                method=request.method,
                path=sanitized,
                error=str(exc),
            )
            from starlette.responses import JSONResponse
            return JSONResponse({"detail": "Internal server error"}, status_code=500)

        duration_ms = round((time.perf_counter() - start) * 1000, 1)
        logger.info(
            "request",
            method=request.method,
            path=sanitized,
            status=response.status_code,
            duration_ms=duration_ms,
        )
        return response
