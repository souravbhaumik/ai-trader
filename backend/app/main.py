"""FastAPI application factory."""
from __future__ import annotations

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.logging_config import configure_logging
from app.core.redis_client import close_redis, init_redis
from app.middleware.logging_middleware import LoggingMiddleware

configure_logging()
logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("startup", environment=settings.environment)
    await init_redis()
    yield
    await close_redis()
    logger.info("shutdown")


def create_app() -> FastAPI:
    from app.api.v1 import router as v1_router

    app = FastAPI(
        title="AI Trader API",
        version="1.0.0",
        docs_url="/docs" if settings.environment == "development" else None,
        redoc_url=None,
        lifespan=lifespan,
    )

    # ── CORS ──────────────────────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Request logging ────────────────────────────────────────────────────────
    app.add_middleware(LoggingMiddleware)

    # ── Routes ─────────────────────────────────────────────────────────────────
    app.include_router(v1_router)

    return app


app = create_app()
