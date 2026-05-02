"""Admin pipeline API — trigger and monitor Celery data tasks.

Endpoints
---------
POST  /admin/pipeline/backfill              Enqueue historical OHLCV backfill (NSE Bhavcopy)
GET   /admin/pipeline/backfill/progress     Poll backfill progress from Redis
POST  /admin/pipeline/bhavcopy              Trigger NSE Bhavcopy ingest (manual)
POST  /admin/pipeline/broker-backfill       Trigger broker API historical backfill (stub)
POST  /admin/pipeline/eod-ingest            Manually trigger EOD data pull
POST  /admin/pipeline/generate-signals      Manually trigger signal generation
POST  /admin/pipeline/train-model           Enqueue LightGBM training run
GET   /admin/pipeline/models                List all trained model versions
POST  /admin/pipeline/models/{id}/promote   Promote a model to active
POST  /admin/pipeline/models/{id}/rollback  Deactivate a model
POST  /admin/pipeline/populate-universe     Populate stock_universe from NSE master CSV
POST  /admin/pipeline/fno-ingest            Manually trigger F&O PCR/OI data pull  [Phase 10]
POST  /admin/pipeline/meta-learner          Manually trigger weight optimization    [Phase 10]
GET   /admin/pipeline/status                Get last-run status for all pipeline tasks
"""
from __future__ import annotations

import json
from typing import Any, Dict, Literal, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.api.v1.deps import require_admin

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/admin/pipeline", tags=["admin-pipeline"])


# -- Schemas -------------------------------------------------------------------

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


# -- Helpers -------------------------------------------------------------------

def _get_sync_redis():
    """Synchronous Redis client for reading progress key (fire-and-forget reads)."""
    import redis as sync_redis
    from app.core.config import settings
    return sync_redis.from_url(settings.redis_url, decode_responses=True)


# -- Endpoints -----------------------------------------------------------------

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
    """Enqueue a historical OHLCV backfill via NSE Bhavcopy (admin only)."""
    await require_admin(request, session)

    try:
        from app.tasks.backfill import backfill_universe
        result = backfill_universe.delay(period=body.period, force=body.force)
    except Exception as exc:
        logger.error("pipeline.backfill_enqueue_failed", err=str(exc))
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Could not reach Celery broker. Is the worker running?",
        )

    logger.info("pipeline.backfill_queued", task_id=result.id, period=body.period)
    return TaskEnqueuedResponse(
        task_id=result.id,
        message=f"NSE Bhavcopy backfill ({body.period}) enqueued. Poll /progress to track.",
    )


@router.get("/backfill/progress", response_model=BackfillProgress)
async def backfill_progress(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Return the current backfill progress from Redis (admin only)."""
    await require_admin(request, session)

    try:
        r = _get_sync_redis()
        raw = r.get("backfill:progress")
    except Exception as exc:
        logger.warning("pipeline.progress_redis_error", err=str(exc))
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
    await require_admin(request, session)

    try:
        from app.tasks.eod_ingest import ingest_eod
        result = ingest_eod.delay()
    except Exception as exc:
        logger.error("pipeline.eod_ingest_enqueue_failed", err=str(exc))
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
    await require_admin(request, session)

    try:
        from app.tasks.signal_generator import generate_signals
        result = generate_signals.delay()
    except Exception as exc:
        logger.error("pipeline.signal_gen_enqueue_failed", err=str(exc))
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Could not reach Celery broker. Is the worker running?",
        )

    logger.info("pipeline.signal_gen_queued", task_id=result.id)
    return TaskEnqueuedResponse(
        task_id=result.id,
        message="Signal generation task enqueued.",
    )


# -- Phase 3: ML model management ----------------------------------------------

class ModelInfo(BaseModel):
    id: str
    model_type: str
    version: str
    is_active: bool
    metrics: dict
    artifact_path: str
    trained_at: str
    promoted_at: Optional[str] = None
    notes: Optional[str] = None


@router.post(
    "/feature-engineering",
    response_model=TaskEnqueuedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_feature_engineering(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Enqueue feature engineering validation across all active symbols (admin only)."""
    await require_admin(request, session)

    try:
        from app.tasks.feature_engineering import run_feature_engineering
        result = run_feature_engineering.delay()
    except Exception as exc:
        logger.error("pipeline.feature_engineering_enqueue_failed", err=str(exc))
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Could not reach Celery broker. Is the worker running?",
        )

    logger.info("pipeline.feature_engineering_queued", task_id=result.id)
    return TaskEnqueuedResponse(
        task_id=result.id,
        message="Feature engineering check enqueued.",
    )


@router.post(
    "/train-model",
    response_model=TaskEnqueuedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_model_training(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Enqueue a LightGBM training run (admin only).

    Training runs asynchronously. Poll ``GET /admin/pipeline/models`` to see
    when the new version appears and then promote it.
    """
    await require_admin(request, session)

    try:
        from app.tasks.ml_training import train_model
        result = train_model.delay()
    except Exception as exc:
        logger.error("pipeline.train_model_enqueue_failed", err=str(exc))
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Could not reach Celery broker. Is the worker running?",
        )

    logger.info("pipeline.train_model_queued", task_id=result.id)
    return TaskEnqueuedResponse(
        task_id=result.id,
        message="Model training enqueued. This may take several minutes.",
    )


@router.get("/models", response_model=list[ModelInfo])
async def list_models(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """List all trained model versions (admin only)."""
    await require_admin(request, session)

    from sqlalchemy import text as _text
    rows = (await session.execute(
        _text("""
            SELECT id, model_type, version, is_active, metrics,
                   artifact_path, trained_at, promoted_at, notes
            FROM   ml_models
            ORDER  BY trained_at DESC
            LIMIT  50
        """)
    )).fetchall()

    return [
        ModelInfo(
            id=str(r[0]),
            model_type=r[1],
            version=r[2],
            is_active=r[3],
            metrics=r[4] or {},
            artifact_path=r[5],
            trained_at=r[6].isoformat() if hasattr(r[6], "isoformat") else str(r[6]),
            promoted_at=r[7].isoformat() if r[7] and hasattr(r[7], "isoformat") else None,
            notes=r[8],
        )
        for r in rows
    ]


@router.post("/models/{model_id}/promote", response_model=ModelInfo)
async def promote_model(
    model_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Promote a trained model to active (admin only).

    Deactivates the currently active model of the same type and marks
    this version as active. The signal generator reloads it within 5 min
    (or immediately if the worker is restarted).
    """
    await require_admin(request, session)

    from sqlalchemy import text as _text

    # Resolve admin user ID from token
    auth_hdr = request.headers.get("Authorization", "")
    token    = auth_hdr.removeprefix("Bearer ").strip()
    from app.core.security import decode_access_token
    payload  = decode_access_token(token)
    admin_id = payload.get("sub")

    row = (await session.execute(
        _text("SELECT id, model_type, version FROM ml_models WHERE id = :mid"),
        {"mid": model_id},
    )).fetchone()

    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Model {model_id} not found.")

    m_type = row[1]

    # Deactivate current active model of same type
    await session.execute(
        _text("UPDATE ml_models SET is_active = FALSE WHERE model_type = :t AND is_active = TRUE"),
        {"t": m_type},
    )

    # Promote requested model
    await session.execute(
        _text("""
            UPDATE ml_models
            SET    is_active = TRUE, promoted_at = NOW(), promoted_by = :admin_id
            WHERE  id = :mid
        """),
        {"mid": model_id, "admin_id": admin_id},
    )
    await session.commit()

    # Force the in-process model loader to reload immediately
    try:
        from app.services.ml_loader import force_reload
        force_reload()
    except Exception:
        pass  # loader may not be warmed in the API process � that's fine

    logger.info("pipeline.model_promoted", model_id=model_id, model_type=m_type, promoted_by=admin_id)

    result = (await session.execute(
        _text("""
            SELECT id, model_type, version, is_active, metrics,
                   artifact_path, trained_at, promoted_at, notes
            FROM   ml_models WHERE id = :mid
        """),
        {"mid": model_id},
    )).fetchone()

    return ModelInfo(
        id=str(result[0]), model_type=result[1], version=result[2],
        is_active=result[3], metrics=result[4] or {},
        artifact_path=result[5],
        trained_at=result[6].isoformat() if hasattr(result[6], "isoformat") else str(result[6]),
        promoted_at=result[7].isoformat() if result[7] else None,
        notes=result[8],
    )


@router.post("/models/{model_id}/rollback")
async def rollback_model(
    model_id: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Deactivate the specified model, reverting to technical-only signals (admin only)."""
    await require_admin(request, session)

    from sqlalchemy import text as _text

    result = await session.execute(
        _text("UPDATE ml_models SET is_active = FALSE WHERE id = :mid RETURNING id, version"),
        {"mid": model_id},
    )
    row = result.fetchone()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Model {model_id} not found.")

    await session.commit()

    try:
        from app.services.ml_loader import force_reload
        force_reload()
    except Exception:
        pass

    logger.info("pipeline.model_rolled_back", model_id=model_id, version=row[1])
    return {"status": "rolled_back", "model_id": str(row[0]), "version": row[1]}


# -- NSE Bhavcopy ingest -------------------------------------------------------

class BhavcopRequest(BaseModel):
    trade_date: Optional[str] = None   # ISO date string; defaults to today in the task


@router.post(
    "/bhavcopy",
    response_model=TaskEnqueuedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_bhavcopy(
    body: BhavcopRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Manually trigger an NSE Bhavcopy ingest for a given date (admin only)."""
    await require_admin(request, session)

    try:
        from app.tasks.bhavcopy import ingest_bhavcopy
        result = ingest_bhavcopy.delay(trade_date_str=body.trade_date)
    except Exception as exc:
        logger.error("pipeline.bhavcopy_enqueue_failed", err=str(exc))
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Could not reach Celery broker. Is the worker running?",
        )

    date_label = body.trade_date or "today"
    logger.info("pipeline.bhavcopy_queued", task_id=result.id, date=date_label)
    return TaskEnqueuedResponse(
        task_id=result.id,
        message=f"Bhavcopy ingest enqueued for {date_label}.",
    )


# -- Broker API historical backfill --------------------------------------------

class BrokerBackfillRequest(BaseModel):
    period: Literal["1y", "2y", "5y"] = "1y"


@router.post(
    "/broker-backfill",
    response_model=TaskEnqueuedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_broker_backfill(
    body: BrokerBackfillRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Enqueue a broker API historical OHLCV backfill (admin only).

    Returns a clear error until BROKER_NAME is set in .env.
    """
    await require_admin(request, session)

    try:
        from app.tasks.broker_backfill import run_broker_backfill
        result = run_broker_backfill.delay(period=body.period)
    except Exception as exc:
        logger.error("pipeline.broker_backfill_enqueue_failed", err=str(exc))
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Could not reach Celery broker. Is the worker running?",
        )

    logger.info("pipeline.broker_backfill_queued", task_id=result.id, period=body.period)
    return TaskEnqueuedResponse(
        task_id=result.id,
        message=(
            f"Broker backfill ({body.period}) enqueued. "
            "Will fail until BROKER_NAME is set in .env."
        ),
    )


# -- Universe population -------------------------------------------------------

class PopulateUniverseRequest(BaseModel):
    nifty500_only: bool = False


@router.post(
    "/populate-universe",
    response_model=TaskEnqueuedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_populate_universe(
    body: PopulateUniverseRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Populate (or refresh) stock_universe from NSE master CSV (admin only).

    Safe to re-run: uses ON CONFLICT DO UPDATE.
    nifty500_only=True inserts only Nifty 500 symbols (~500 rows, fast).
    nifty500_only=False inserts the full NSE EQ universe (~2100 rows).
    """
    await require_admin(request, session)

    try:
        from app.tasks.universe_population import populate_universe
        result = populate_universe.delay(nifty500_only=body.nifty500_only)
    except Exception as exc:
        logger.error("pipeline.populate_universe_enqueue_failed", err=str(exc))
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Could not reach Celery broker. Is the worker running?",
        )

    scope = "Nifty 500 only" if body.nifty500_only else "full NSE universe"
    logger.info("pipeline.populate_universe_queued", task_id=result.id, scope=scope)
    return TaskEnqueuedResponse(
        task_id=result.id,
        message=f"Universe population enqueued ({scope}). Takes ~30�60 seconds.",
    )


# -- Logo download -------------------------------------------------------------

@router.post(
    "/download-logos",
    response_model=TaskEnqueuedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_download_logos(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Download logos for all active symbols from logo.dev and update logo_path in DB.

    Safe to re-run: symbols that already have a cached PNG are skipped.
    New symbols added to stock_universe will get their logos on the next run.
    """
    await require_admin(request, session)

    try:
        from app.tasks.download_logos import download_logos
        result = download_logos.delay()
    except Exception as exc:
        logger.error("pipeline.download_logos_enqueue_failed", err=str(exc))
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Could not reach Celery broker. Is the worker running?",
        )

    logger.info("pipeline.download_logos_enqueued", task_id=result.id)
    return TaskEnqueuedResponse(
        task_id=result.id,
        message="Logo download enqueued. Check Admin → Pipeline → Step 11 logs for progress.",
    )


# -- Phase 10: F&O Ingest ------------------------------------------------------

@router.post(
    "/fno-ingest",
    response_model=TaskEnqueuedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_fno_ingest(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Manually trigger F&O (PCR/OI) data pull for all F&O-enabled symbols (admin only)."""
    await require_admin(request, session)

    try:
        from app.tasks.fno_ingest import ingest_fno_data
        result = ingest_fno_data.delay()
    except Exception as exc:
        logger.error("pipeline.fno_ingest_enqueue_failed", err=str(exc))
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Could not reach Celery broker. Is the worker running?",
        )

    logger.info("pipeline.fno_ingest_queued", task_id=result.id)
    return TaskEnqueuedResponse(
        task_id=result.id,
        message="F&O ingest enqueued. PCR/OI data will be cached in Redis within ~60s.",
    )


# -- Phase 10: Meta-Learner weight optimization --------------------------------

@router.post(
    "/meta-learner",
    response_model=TaskEnqueuedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_meta_learner(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Manually trigger the Meta-Learner 30-day weight optimization (admin only).

    Audits the last 30 days of signal_outcomes, runs a grid search over
    component weights (Tech, ML, Sentiment, F&O), and persists the optimal
    weights to Redis (system:dynamic_weights).
    """
    await require_admin(request, session)

    try:
        from app.tasks.meta_learner import optimize_weights
        result = optimize_weights.delay()
    except Exception as exc:
        logger.error("pipeline.meta_learner_enqueue_failed", err=str(exc))
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Could not reach Celery broker. Is the worker running?",
        )

    logger.info("pipeline.meta_learner_queued", task_id=result.id)
    return TaskEnqueuedResponse(
        task_id=result.id,
        message="Meta-Learner optimization enqueued. system:dynamic_weights updates on completion.",
    )


_ALL_TASK_NAMES = [
    "universe_population",
    "broker_backfill",
    "bhavcopy",
    "backfill",
    "feature_engineering",
    "eod_ingest",
    "fno_ingest",        # Phase 10: F&O PCR/OI ingestion
    "meta_learner",      # Phase 10: dynamic weight optimization
    "ml_training",
    "signal_generator",
    "news_sentiment",
    "logo_download",
]


class TaskStatusEntry(BaseModel):
    task_name:   str
    status:      str
    message:     str
    started_at:  Optional[str] = None
    finished_at: Optional[str] = None
    summary:     dict = {}
    ts:          Optional[str] = None


class TaskLogEntry(BaseModel):
    ts:    str
    level: str   # "info" | "error" | "warn"
    msg:   str


@router.get("/status", response_model=list[TaskStatusEntry])
async def pipeline_status(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Return last-run status for every pipeline task (admin only)."""
    await require_admin(request, session)

    from app.tasks.task_utils import read_all_task_statuses

    entries = read_all_task_statuses(_ALL_TASK_NAMES)
    return [TaskStatusEntry(**e) for e in entries]


@router.get("/{task_name}/logs", response_model=list[TaskLogEntry])
async def task_logs(
    task_name: str,
    request:   Request,
    limit:     int = Query(default=200, ge=1, le=500),
    session:   AsyncSession = Depends(get_session),
):
    """Return the last *limit* log lines for a pipeline task (admin only)."""
    await require_admin(request, session)

    if task_name not in _ALL_TASK_NAMES:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"Unknown task '{task_name}'. Valid tasks: {_ALL_TASK_NAMES}",
        )

    from app.tasks.task_utils import read_task_logs

    entries = read_task_logs(task_name, limit=limit)
    return [
        TaskLogEntry(
            ts=e.get("ts", ""),
            level=e.get("level", "info"),
            msg=e.get("msg", ""),
        )
        for e in entries
    ]

