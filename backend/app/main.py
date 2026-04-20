"""FastAPI application factory."""
from __future__ import annotations

import asyncio
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


def _download_ml_models() -> None:
    """Download LSTM and TFT model artifacts from Google Drive if not present.

    Reads LSTM_GDRIVE_ID and TFT_GDRIVE_ID from settings.  No-ops silently
    when the ID is empty or the file already exists (Docker volume persists
    across restarts so we only pay the download cost once per fresh volume).
    """
    import gdown  # noqa: PLC0415 — optional dep; present in requirements.txt

    downloads = [
        (settings.lstm_gdrive_id, "/app/models/lstm/latest.pt", "lstm"),
        (settings.tft_gdrive_id,  "/app/models/tft/latest.pt",  "tft"),
    ]
    for gdrive_id, dest_path, name in downloads:
        if not gdrive_id:
            logger.info(f"startup.model_skip.no_id", model=name)
            continue
        from pathlib import Path  # noqa: PLC0415
        dest = Path(dest_path)
        if dest.exists():
            logger.info("startup.model_cached", model=name, path=dest_path)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        url = f"https://drive.google.com/uc?id={gdrive_id}"
        logger.info("startup.model_download_start", model=name, gdrive_id=gdrive_id)
        try:
            gdown.download(url, str(dest), quiet=False)
            logger.info("startup.model_download_done", model=name, path=dest_path)
        except Exception as exc:
            logger.warning("startup.model_download_failed", model=name, err=str(exc))


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("startup", environment=settings.environment)
    await init_redis()

    # Download LSTM / TFT model artifacts from Google Drive (first boot only).
    try:
        _download_ml_models()
    except Exception as exc:
        logger.warning("startup.download_ml_models_failed", err=str(exc))

    # Any task still marked 'running' at startup was interrupted by a shutdown.
    # Reset those rows to 'unknown' so the UI doesn't show stale RUNNING badges.
    try:
        from app.tasks.task_utils import reset_interrupted_tasks
        reset_interrupted_tasks()
    except Exception as exc:
        logger.warning("startup.reset_interrupted_tasks_failed", err=str(exc))

    # Start the single shared price broadcaster for WebSocket fan-out
    from app.api.v1.ws import price_broadcaster, signal_broadcaster
    broadcaster_task = asyncio.create_task(price_broadcaster())
    signal_task      = asyncio.create_task(signal_broadcaster())

    yield

    broadcaster_task.cancel()
    signal_task.cancel()
    for t in (broadcaster_task, signal_task):
        try:
            await t
        except asyncio.CancelledError:
            pass
    await close_redis()
    logger.info("shutdown")


def create_app() -> FastAPI:
    from app.api.v1 import router as v1_router
    from app.core.rate_limiter import limiter
    from slowapi import _rate_limit_exceeded_handler
    from slowapi.errors import RateLimitExceeded

    app = FastAPI(
        title="AI Trader API",
        version="1.0.0",
        docs_url="/docs" if settings.environment == "development" else None,
        redoc_url=None,
        lifespan=lifespan,
    )

    # ── Rate Limiting ──────────────────────────────────────────────────────────
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

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
