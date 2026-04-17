"""Celery application — Phase 2 background task queue.

Broker: Redis (same instance used for JWT blocklist and caching).
All tasks run as regular (non-async) functions in separate worker processes.
"""
from __future__ import annotations

from celery import Celery

from app.core.config import settings

# Use Redis DB 1 for Celery to keep it separate from the app cache (DB 0)
_broker_url  = settings.redis_url.replace("/0", "/1")
_backend_url = settings.redis_url.replace("/0", "/2")

celery_app = Celery(
    "ai_trader",
    broker=_broker_url,
    backend=_backend_url,
    include=[
        "app.tasks.backfill",
        "app.tasks.eod_ingest",
        "app.tasks.signal_generator",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Kolkata",
    enable_utc=False,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,

    # Beat schedule (periodic tasks)
    beat_schedule={
        # EOD data ingestion — runs at 4:30 PM IST Mon–Fri
        "eod-ingest-daily": {
            "task":     "app.tasks.eod_ingest.ingest_eod",
            "schedule": {"hour": 16, "minute": 30},  # crontab below
        },
    },
)

# ── Celery Beat crontab (IST — Celery uses tz-aware schedules) ────────────────
from celery.schedules import crontab  # noqa: E402

celery_app.conf.beat_schedule = {
    "eod-ingest-daily": {
        "task":     "app.tasks.eod_ingest.ingest_eod",
        "schedule": crontab(hour=16, minute=30, day_of_week="1-5"),  # Mon–Fri 4:30 PM IST
    },
    "signal-generation-daily": {
        "task":     "app.tasks.signal_generator.generate_signals",
        "schedule": crontab(hour=16, minute=45, day_of_week="1-5"),  # Mon–Fri 4:45 PM IST
    },
}
