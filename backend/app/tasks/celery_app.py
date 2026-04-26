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
        "app.tasks.feature_engineering",
        "app.tasks.ml_training",
        "app.tasks.news_sentiment",
        "app.tasks.signal_generator",
        "app.tasks.signal_outcome_evaluation",
        "app.tasks.universe_population",
        "app.tasks.webhook_retry",
        "app.tasks.eod_reconciliation",
        "app.tasks.explain_signal",
        "app.tasks.download_logos",
        "app.tasks.macro_pulse",
        "app.tasks.broker_reconnect",
        "app.tasks.intraday_ingest",
        "app.tasks.intraday_signal_generator",
        "app.tasks.upstox_token_refresh",
        "app.tasks.fundamentals_ingest",
        "app.tasks.breaking_news_scanner",
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
    # Pre-market signal generation — 8:30 AM IST (before market opens at 9:15)
    "signal-generation-premarket": {
        "task":     "app.tasks.signal_generator.generate_signals",
        "schedule": crontab(hour=8, minute=30, day_of_week="1-5"),  # Mon–Fri 8:30 AM IST
    },
    # Post-market signal generation — 4:45 PM IST (after market closes)
    "signal-generation-daily": {
        "task":     "app.tasks.signal_generator.generate_signals",
        "schedule": crontab(hour=16, minute=45, day_of_week="1-5"),  # Mon–Fri 4:45 PM IST
    },
    # Signal outcome evaluation — 5:00 PM IST (after EOD data)
    "signal-outcome-evaluation": {
        "task":     "app.tasks.signal_outcome_evaluation.evaluate_signal_outcomes",
        "schedule": crontab(hour=17, minute=0, day_of_week="1-5"),  # Mon–Fri 5:00 PM IST
    },
    # Signal outcome morning fill — 8:20 AM IST (fills prev-day actual prices from bhavcopy)
    # Runs before signal generation (8:30 AM) so analytics are fresh at open.
    "signal-outcome-morning": {
        "task":     "app.tasks.signal_outcome_evaluation.evaluate_signal_outcomes",
        "schedule": crontab(hour=8, minute=20, day_of_week="1-5"),  # Mon–Fri 8:20 AM IST
    },
    # News sentiment pipeline — every 15 min during market hours + post-market
    # Extended to 16:15 to capture post-close results and corporate announcements.
    "news-sentiment-pipeline": {
        "task":     "app.tasks.news_sentiment.fetch_news_sentiment",
        "schedule": crontab(hour="9-16", minute="*/15", day_of_week="1-5"),
    },
    # Pre-market news warm-up — 7:30 AM IST (Asian market open, FII pre-data, overnight news)
    # Seeds the sentiment cache before the broker reconnect (8:00 AM) and signals (8:30 AM).
    "news-sentiment-premarket": {
        "task":     "app.tasks.news_sentiment.fetch_news_sentiment",
        "schedule": crontab(hour=7, minute=30, day_of_week="1-5"),
    },
    # Pre-signal warm-up — 8:20 AM IST (10 min before pre-market signal generation)
    # Ensures sentiment cache is maximally fresh for the 8:30 AM signal run.
    "news-sentiment-pre-signal": {
        "task":     "app.tasks.news_sentiment.fetch_news_sentiment",
        "schedule": crontab(hour=8, minute=20, day_of_week="1-5"),
    },
    # Post-market news run — 4:40 PM IST (5 min before EOD signal generation at 4:45 PM)
    # Captures post-close earnings, corporate actions, and end-of-session FII/DII data.
    "news-sentiment-post-market": {
        "task":     "app.tasks.news_sentiment.fetch_news_sentiment",
        "schedule": crontab(hour=16, minute=40, day_of_week="1-5"),
    },
    # Breaking news fast-path scanner — every 2 minutes during market hours.
    # Keyword-matches 4 fast free sources; triggers full sentiment fetch on high-impact hits.
    # Reduces breaking news latency from 15 minutes → ~2 minutes.
    "breaking-news-scanner": {
        "task":     "app.tasks.breaking_news_scanner.scan_breaking_news",
        "schedule": crontab(hour="9-16", minute="*/2", day_of_week="1-5"),
    },
    # LightGBM weekly retrain — Saturday 2:00 AM IST (off-market, low load)
    "lgbm-weekly-retrain": {
        "task":     "app.tasks.ml_training.train_model",
        "schedule": crontab(hour=2, minute=0, day_of_week="6"),  # Saturday 2:00 AM IST
    },
    # EOD live-order reconciliation — 4:00 PM IST Mon–Fri
    # NSE closes at 3:30 PM IST; this fires 30 minutes after close to allow
    # exchange confirmations to propagate before reconciling order status.
    "eod-live-order-reconciliation": {
        "task":     "app.tasks.eod_reconciliation.reconcile_live_orders",
        "schedule": crontab(hour=16, minute=0, day_of_week="1-5"),
    },
    # Macro pulse — every 30 min during market hours
    "macro-pulse-pipeline": {
        "task":     "app.tasks.macro_pulse.update_macro_regime",
        "schedule": crontab(hour="9-16", minute="*/30", day_of_week="1-5"),
    },
    # Daily Angel One session refresh before market open
    "broker-reconnect-daily": {
        "task":     "app.tasks.broker_reconnect.refresh_broker_sessions",
        "schedule": crontab(hour=8, minute=0, day_of_week="1-5"),
    },
    # ── Intraday data + signals ───────────────────────────────────────────────
    # Intraday OHLCV ingest — every 15 min during market hours.
    # NSE closes at 15:30; using hour="9-14" + explicit 15:00,15:15 avoids
    # firing no-op ticks at 15:30, 15:45 or 15:59.
    "intraday-ohlcv-ingest": {
        "task":     "app.tasks.intraday_ingest.ingest_intraday",
        "schedule": crontab(hour="9-14", minute="*/15", day_of_week="1-5"),
    },
    "intraday-ohlcv-ingest-1500": {
        "task":     "app.tasks.intraday_ingest.ingest_intraday",
        "schedule": crontab(hour=15, minute="0,15", day_of_week="1-5"),
    },
    # Intraday signals — every 15 min from 9:30 AM to 3:15 PM IST
    # (9:15 AM opening bar is too thin; 3:30 PM close is handled by EOD)
    "intraday-signal-15min": {
        "task":     "app.tasks.intraday_signal_generator.generate_intraday_signals",
        "schedule": crontab(hour="9-15", minute="30,45,0,15", day_of_week="1-5"),
    },
    # Fundamentals refresh — daily at 7:00 AM IST (pre-market, uses yfinance)
    "fundamentals-daily": {
        "task":     "app.tasks.fundamentals_ingest.refresh_fundamentals",
        "schedule": crontab(hour=7, minute=0, day_of_week="1-5"),
    },
    # Upstox token validity check — 7:30 AM (before market open)
    "upstox-token-check": {
        "task":     "app.tasks.upstox_token_refresh.check_upstox_tokens",
        "schedule": crontab(hour=7, minute=30, day_of_week="1-5"),
    },
}


# ── Worker startup hook — lazy model loading ──────────────────────────────────
# Models are loaded on first inference call rather than at worker startup.
# This avoids VRAM/RAM conflicts on small VPS machines and prevents heartbeat
# timeouts during cold-start.  The _maybe_reload() pattern in lstm_service
# and tft_service handles warm-up on first call.

from celery.signals import worker_ready  # noqa: E402


@worker_ready.connect
def _on_worker_ready(sender, **kwargs):  # noqa: ANN001, ANN002, ANN003
    """Log worker readiness; models load lazily on first inference."""
    import logging  # noqa: PLC0415
    _log = logging.getLogger(__name__)
    _log.info("celery_worker.ready (models will load on first inference)")
