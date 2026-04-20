"""Celery task — asynchronous LLM explanation for a trading signal.

This task is intentionally queued to the ``low_priority`` queue so it never
delays real-time signal delivery or paper-trade execution.

Pipeline
--------
1. Load the signal row from DB (id, symbol, signal_type, confidence, features).
2. Fetch company name from ``stock_universe``.
3. Fetch the 3 most-recent news headlines for the symbol from ``news_sentiment``.
4. Read macro regime + top events from Redis (``macro:sentiment:regime`` /
   ``macro:top_events``).  Falls back to "neutral" / [] when absent.
5. Call ``explainer.explain()`` cascade (Groq → Gemini → Local → None).
6. Write the result back to ``signals.explanation`` (UPDATE, best-effort).

All failures are swallowed — a missing explanation is never surfaced as an
error to the user.
"""
from __future__ import annotations

import json
from typing import Optional

import structlog

from sqlalchemy import text

from app.core.database import get_sync_session
from app.tasks.celery_app import celery_app

logger = structlog.get_logger(__name__)

_TASK_NAME = "explain_signal"


@celery_app.task(
    name="app.tasks.explain_signal.explain_signal",
    bind=True,
    max_retries=2,
    default_retry_delay=60,
    queue="low_priority",
    acks_late=True,
    ignore_result=True,
)
def explain_signal(self, signal_id: str) -> None:
    """Generate and persist an LLM explanation for the given signal ID."""
    try:
        _do_explain(signal_id)
    except Exception as exc:
        logger.error("explain_signal.unexpected_error", signal_id=signal_id, err=str(exc))
        # Retry on unexpected failure (network blip, etc.), but never block
        try:
            raise self.retry(exc=exc)
        except self.MaxRetriesExceededError:
            logger.warning("explain_signal.max_retries_exceeded", signal_id=signal_id)


def _do_explain(signal_id: str) -> None:
    """Core logic — separated so it can be unit-tested without Celery."""
    from app.services.explainer import explain
    from app.core.config import settings

    if settings.explainability_backend == "disabled":
        return

    with get_sync_session() as session:
        # ── 1. Load signal ─────────────────────────────────────────────────────
        row = session.execute(
            text("""
                SELECT symbol, signal_type, confidence, features, explanation
                FROM   signals
                WHERE  id = :id
            """),
            {"id": signal_id},
        ).fetchone()

        if row is None:
            logger.warning("explain_signal.signal_not_found", signal_id=signal_id)
            return

        symbol, signal_type, confidence, features_raw, existing_explanation = row

        # Skip if already explained (idempotent)
        if existing_explanation:
            return

        if confidence < settings.explainability_confidence_threshold:
            return

        # ── 2. Load company name ───────────────────────────────────────────────
        name_row = session.execute(
            text("SELECT name FROM stock_universe WHERE symbol = :symbol LIMIT 1"),
            {"symbol": symbol},
        ).fetchone()
        company_name = name_row[0] if name_row and name_row[0] else symbol

        # ── 3. Parse features JSON ─────────────────────────────────────────────
        features: dict = {}
        if features_raw:
            try:
                features = json.loads(features_raw) if isinstance(features_raw, str) else dict(features_raw)
            except (json.JSONDecodeError, TypeError):
                pass

        # ── 4. Fetch top 3 headlines for this symbol ───────────────────────────
        headline_rows = session.execute(
            text("""
                SELECT headline
                FROM   news_sentiment
                WHERE  symbol = :symbol
                ORDER  BY published_at DESC
                LIMIT  3
            """),
            {"symbol": symbol},
        ).fetchall()
        headlines = [r[0] for r in headline_rows if r[0]]

        # ── 5. Read macro context from Redis ──────────────────────────────────
        macro_regime = "neutral"
        macro_events: list[str] = []
        try:
            import redis as _redis
            from app.core.config import settings as _cfg
            r = _redis.from_url(_cfg.redis_url, decode_responses=True)
            regime_val = r.get("macro:sentiment:regime")
            if regime_val:
                macro_regime = regime_val.strip()
            events_val = r.get("macro:top_events")
            if events_val:
                macro_events = json.loads(events_val)
                if not isinstance(macro_events, list):
                    macro_events = []
            r.close()
        except Exception as exc:
            logger.debug("explain_signal.redis_macro_failed", err=str(exc))

        # ── 6. Call LLM cascade ────────────────────────────────────────────────
        explanation: Optional[str] = explain(
            symbol=symbol,
            company_name=company_name,
            signal_type=signal_type,
            confidence=float(confidence),
            features=features,
            headlines=headlines,
            macro_regime=macro_regime,
            macro_events=macro_events,
        )

        if explanation is None:
            logger.info("explain_signal.no_explanation_generated", signal_id=signal_id, symbol=symbol)
            return

        # ── 7. Persist explanation ─────────────────────────────────────────────
        session.execute(
            text("""
                UPDATE signals
                SET    explanation = :explanation
                WHERE  id = :id
            """),
            {"explanation": explanation, "id": signal_id},
        )
        session.commit()
        logger.info(
            "explain_signal.done",
            signal_id=signal_id,
            symbol=symbol,
            chars=len(explanation),
        )
