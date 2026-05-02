"""F&O data ingestion task — Phase 10 Institutional Upgrade.

Fetches PCR and OI data for all active symbols and caches them in Redis.
Runs daily at 6:30 PM IST (after NSE F&O data is published post-close).

Non-F&O symbols return None from fno_service and are counted as errors —
this is expected and logged but does not fail the task.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta

import structlog
from sqlalchemy import text

from app.core.database import get_sync_session
from app.tasks.celery_app import celery_app

logger = structlog.get_logger(__name__)

_TASK = "fno_ingest"
_IST = timezone(timedelta(hours=5, minutes=30))
_CHUNK_SIZE = 5        # concurrent requests per batch (polite to NSE)
_CHUNK_DELAY = 1.0     # seconds between chunks


@celery_app.task(name="app.tasks.fno_ingest.ingest_fno_data", bind=True)
def ingest_fno_data(self):
    """Fetch and cache F&O metrics (PCR, OI) for all active symbols."""
    from app.tasks.task_utils import (
        write_task_status, now_iso, clear_task_logs, append_task_log,
    )
    from app.services.fno_service import get_fno_metrics_cached

    started = now_iso()
    clear_task_logs(_TASK)
    write_task_status(_TASK, "running", "F&O data ingestion started.", started_at=started)

    with get_sync_session() as session:
        symbols: list[str] = [
            row[0] for row in session.execute(
                text("SELECT symbol FROM stock_universe WHERE is_active = TRUE ORDER BY market_cap DESC NULLS LAST")
            ).fetchall()
        ]

    total = len(symbols)
    count = 0
    errors = 0

    append_task_log(_TASK, f"Starting F&O ingest for {total} symbols in chunks of {_CHUNK_SIZE}...")

    async def _process_all() -> None:
        nonlocal count, errors
        for i in range(0, len(symbols), _CHUNK_SIZE):
            chunk = symbols[i: i + _CHUNK_SIZE]
            results = await asyncio.gather(
                *[get_fno_metrics_cached(s, force_refresh=True) for s in chunk],
                return_exceptions=True,
            )
            for res in results:
                if isinstance(res, dict):
                    count += 1
                else:
                    errors += 1  # None = no F&O for this symbol; Exception = network err

            if i % (_CHUNK_SIZE * 10) == 0 and i > 0:
                logger.info("fno_ingest.progress", processed=i + len(chunk), total=total)

            await asyncio.sleep(_CHUNK_DELAY)

    asyncio.run(_process_all())

    msg = f"F&O ingest complete. Cached: {count}, No-F&O/Error: {errors}, Total: {total}"
    logger.info("fno_ingest.done", cached=count, errors=errors, total=total)
    write_task_status(
        _TASK, "done", msg,
        started_at=started, finished_at=now_iso(),
        summary={"cached": count, "errors": errors, "total": total},
    )
    return {"status": "ok", "cached": count, "errors": errors}
