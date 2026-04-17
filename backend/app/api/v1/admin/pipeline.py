"""Admin pipeline API — trigger and monitor Celery data tasks.

Endpoints
---------
POST  /admin/pipeline/backfill           Enqueue a full historical backfill
GET   /admin/pipeline/backfill/progress  Poll progress stored in Redis
POST  /admin/pipeline/eod-ingest         Manually trigger an EOD data pull
POST  /admin/pipeline/generate-signals   Manually trigger signal generation
"""
from __future__ import annotations

import json
from typing import Any, Dict, Literal, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from jose import JWTError
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.security import decode_access_token

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/admin/pipeline", tags=["admin-pipeline"])


# ── Auth guard (reuse the same pattern as admin/users.py) ────────────────────

async def _require_admin(request: Request, session: AsyncSession) -> None:
    auth_hdr = request.headers.get("Authorization", "")
    if not auth_hdr.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token.")
    token = auth_hdr.removeprefix("Bearer ").strip()
    try:
        payload = decode_access_token(token)
    except JWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token.")
    if payload.get("role") != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin role required.")


# ── Schemas ───────────────────────────────────────────────────────────────────

class BackfillRequest(BaseModel):
    period: Literal["1y", "2y", "5y"] = "2y"
    force: bool = False


class TaskEnqueuedResponse(BaseModel):
    task_id: str
    status: str = "queued"
    message: str


class BackfillProgress(BaseModel):
    pct: int
    message: str
    status: str        # "running" | "done" | "error" | "idle"
    ts: Optional[str] = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_sync_redis():
    """Synchronous Redis client for reading progress key (fire-and-forget reads)."""
    import redis as sync_redis
    from app.core.config import settings
    return sync_redis.from_url(settings.redis_url, decode_responses=True)


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post(
    "/backfill",
    response_model=TaskEnqueuedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_backfill(
    body: BackfillRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Enqueue a historical OHLCV backfill (admin only)."""
    await _require_admin(request, session)

    try:
        from app.tasks.backfill import backfill_universe
        result = backfill_universe.delay(period=body.period, force=body.force)
    except Exception as exc:
        logger.error("pipeline.backfill_enqueue_failed", error=str(exc))
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Could not reach Celery broker. Is the worker running?",
        )

    logger.info("pipeline.backfill_queued", task_id=result.id, period=body.period)
    return TaskEnqueuedResponse(
        task_id=result.id,
        message=f"Backfill ({body.period}) enqueued. Poll /progress to track.",
    )


@router.get("/backfill/progress", response_model=BackfillProgress)
async def backfill_progress(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Return the current backfill progress from Redis (admin only)."""
    await _require_admin(request, session)

    try:
        r = _get_sync_redis()
        raw = r.get("backfill:progress")
    except Exception as exc:
        logger.warning("pipeline.progress_redis_error", error=str(exc))
        return BackfillProgress(pct=0, message="Redis unavailable.", status="error")

    if not raw:
        return BackfillProgress(pct=0, message="No backfill has been run yet.", status="idle")

    try:
        data: Dict[str, Any] = json.loads(raw)
        return BackfillProgress(
            pct=int(data.get("pct", 0)),
            message=str(data.get("message", "")),
            status=str(data.get("status", "unknown")),
            ts=data.get("ts"),
        )
    except (json.JSONDecodeError, ValueError):
        return BackfillProgress(pct=0, message="Malformed progress data.", status="error")


@router.post(
    "/eod-ingest",
    response_model=TaskEnqueuedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_eod_ingest(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Manually trigger an EOD OHLCV data pull (admin only)."""
    await _require_admin(request, session)

    try:
        from app.tasks.eod_ingest import ingest_eod
        result = ingest_eod.delay()
    except Exception as exc:
        logger.error("pipeline.eod_ingest_enqueue_failed", error=str(exc))
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Could not reach Celery broker. Is the worker running?",
        )

    logger.info("pipeline.eod_ingest_queued", task_id=result.id)
    return TaskEnqueuedResponse(
        task_id=result.id,
        message="EOD ingest enqueued successfully.",
    )


@router.post(
    "/generate-signals",
    response_model=TaskEnqueuedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_signal_generation(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Manually trigger the technical-indicator signal generation task (admin only)."""
    await _require_admin(request, session)

    try:
        from app.tasks.signal_generator import generate_signals
        result = generate_signals.delay()
    except Exception as exc:
        logger.error("pipeline.signal_gen_enqueue_failed", error=str(exc))
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Could not reach Celery broker. Is the worker running?",
        )

    logger.info("pipeline.signal_gen_queued", task_id=result.id)
    return TaskEnqueuedResponse(
        task_id=result.id,
        message="Signal generation task enqueued.",
    )
