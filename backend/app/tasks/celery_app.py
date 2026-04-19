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
        "app.tasks.bhavcopy",
        "app.tasks.broker_backfill",
        "app.tasks.eod_ingest",
        "app.tasks.ml_training",
        "app.tasks.news_sentiment",
        "app.tasks.signal_generator",
        "app.tasks.universe_population",
        "app.tasks.webhook_retry",
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
    "bhavcopy-daily": {
        "task":     "app.tasks.bhavcopy.ingest_bhavcopy",
        "schedule": crontab(hour=19, minute=30, day_of_week="1-5"),  # Mon–Fri 7:30 PM IST
    },
    "signal-generation-daily": {
        "task":     "app.tasks.signal_generator.generate_signals",
        "schedule": crontab(hour=16, minute=45, day_of_week="1-5"),  # Mon–Fri 4:45 PM IST
    },
    # News sentiment pipeline — every 15 min during market hours
    "news-sentiment-pipeline": {
        "task":     "app.tasks.news_sentiment.fetch_news_sentiment",
        "schedule": crontab(hour="9-15", minute="*/15", day_of_week="1-5"),  # Mon–Fri 9:00 AM – 3:45 PM IST
    },
    # LightGBM weekly retrain — Saturday 2:00 AM IST (off-market, low load)
    "lgbm-weekly-retrain": {
        "task":     "app.tasks.ml_training.train_model",
        "schedule": crontab(hour=2, minute=0, day_of_week="6"),  # Saturday 2:00 AM IST
    },
}


# ── Worker startup hook — pre-load PyTorch models into memory ─────────────────
# This fires once per worker process after the worker is fully initialised.
# Loading here (rather than on first inference call) avoids a cold-load spike
# during live signal generation and prevents Celery heartbeat timeouts on
# CPU-only VPS machines where torch.load can take 5-15 seconds.

from celery.signals import worker_ready  # noqa: E402


@worker_ready.connect
def _preload_ml_models(sender, **kwargs):  # noqa: ANN001, ANN002, ANN003
    """Eagerly load LSTM and TFT model artifacts when the worker starts."""
    import logging  # noqa: PLC0415
    _log = logging.getLogger(__name__)

    try:
        from app.services.lstm_service import warm_up as lstm_warm_up  # noqa: PLC0415
        ok = lstm_warm_up()
        _log.info("celery_worker.lstm_preload", loaded=ok)
    except Exception as exc:
        _log.warning("celery_worker.lstm_preload_failed", error=str(exc))

    try:
        from app.services.tft_service import warm_up as tft_warm_up  # noqa: PLC0415
        ok = tft_warm_up()
        _log.info("celery_worker.tft_preload", loaded=ok)
    except Exception as exc:
        _log.warning("celery_worker.tft_preload_failed", error=str(exc))
