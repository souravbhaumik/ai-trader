"""Dynamic Meta-Learner task — Phase 10 Institutional Upgrade.

Analyses the last 30 days of evaluated signal outcomes and optimises the
blend weights (W_TECH, W_ML, W_SENTIMENT, W_FNO) via accuracy-maximising
grid search.

Stores optimised weights in Redis at ``system:dynamic_weights`` (7-day TTL).
The Signal Generator loads these weights on each run, falling back to .env
defaults if the key is absent or expired.

Runs Saturday 03:00 AM IST — 1 hour after the weekly LightGBM retrain.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import structlog
from sqlalchemy import text

from app.core.database import get_sync_session
from app.tasks.celery_app import celery_app

logger = structlog.get_logger(__name__)

_TASK = "meta_learner"
_IST = timezone(timedelta(hours=5, minutes=30))
_LOOKBACK_DAYS = 30
_REDIS_TTL = 7 * 24 * 3600   # 7 days — auto-expires stale weights after one missed week
_MIN_WEIGHT = 0.1             # each source must contribute at least 10%


@celery_app.task(name="app.tasks.meta_learner.optimize_weights", bind=True)
def optimize_weights(self):
    """Evaluate historical signal performance and optimise blend weights."""
    from app.tasks.task_utils import (
        write_task_status, now_iso, clear_task_logs, append_task_log,
    )
    import redis as _redis
    from app.core.config import settings

    started = now_iso()
    clear_task_logs(_TASK)
    write_task_status(_TASK, "running", "Meta-Learner weight optimisation started.", started_at=started)

    cutoff = datetime.now(_IST) - timedelta(days=_LOOKBACK_DAYS)
    append_task_log(_TASK, f"Auditing signals since {cutoff.date()}...")

    with get_sync_session() as session:
        rows = session.execute(
            text("""
                SELECT
                    s.features,
                    o.hit_target,
                    o.signal_type
                FROM signals s
                JOIN signal_outcomes o ON s.id = o.signal_id
                WHERE o.signal_ts  >= :cutoff
                  AND o.is_evaluated = TRUE
                  AND (s.features::jsonb) ? 'score_tech'
            """),
            {"cutoff": cutoff},
        ).fetchall()

    if not rows:
        msg = "No evaluated signals with component scores in the last 30 days. Skipping."
        logger.warning("meta_learner.no_data")
        write_task_status(_TASK, "done", msg, started_at=started, finished_at=now_iso())
        return {"status": "skipped", "reason": "no_data"}

    # ── Parse features — JSONB comes back as string from raw text() queries ────
    parsed: list[tuple[dict[str, Any], bool]] = []
    for row in rows:
        feat_raw = row[0]
        # SQLAlchemy text() returns JSONB as a Python str; decode it
        feat = json.loads(feat_raw) if isinstance(feat_raw, str) else feat_raw
        hit_target = bool(row[1])
        parsed.append((feat, hit_target))

    append_task_log(_TASK, f"Analysing {len(parsed)} evaluated signals...")

    # ── Grid search — enumerate weight combos that sum to 1.0 ─────────────────
    # Use integer steps (tenths) to avoid floating-point accumulation errors.
    best_accuracy = -1.0
    best_weights: dict[str, float] = {}

    steps = list(range(1, 8))  # 0.1 … 0.7 in tenths (ensures min weight = 0.1)

    for wt in steps:
        for wm in steps:
            for ws in steps:
                wf_int = 10 - wt - wm - ws
                if wf_int < 1:          # W_FNO must be >= 0.1
                    continue
                if wt + wm + ws + wf_int != 10:
                    continue            # should always pass, but belt-and-braces

                w_tech = wt / 10
                w_ml   = wm / 10
                w_sent = ws / 10
                w_fno  = wf_int / 10

                correct = 0
                for feat, hit_target in parsed:
                    blended = (
                        w_tech * feat.get("score_tech", 0.5) +
                        w_ml   * feat.get("score_ml",   0.5) +
                        w_sent * feat.get("score_sent",  0.5) +
                        w_fno  * feat.get("score_fno",   0.5)
                    )
                    predicted_buy = blended >= 0.5
                    if predicted_buy == hit_target:
                        correct += 1

                accuracy = correct / len(parsed)
                if accuracy > best_accuracy:
                    best_accuracy = accuracy
                    best_weights = {
                        "W_TECH":      w_tech,
                        "W_ML":        w_ml,
                        "W_SENTIMENT": w_sent,
                        "W_FNO":       w_fno,
                    }

    # ── Persist to Redis ───────────────────────────────────────────────────────
    if not best_weights:
        msg = "Grid search produced no valid weight combination."
        logger.error("meta_learner.grid_search_failed")
        write_task_status(_TASK, "error", msg, started_at=started, finished_at=now_iso())
        return {"status": "error", "reason": "grid_search_failed"}

    r = _redis.from_url(settings.redis_url, decode_responses=True)
    try:
        r.setex("system:dynamic_weights", _REDIS_TTL, json.dumps(best_weights))
    finally:
        r.close()

    msg = (
        f"Optimisation complete. Accuracy: {best_accuracy:.2%} on {len(parsed)} samples. "
        f"New weights: {best_weights}"
    )
    append_task_log(_TASK, msg)
    logger.info("meta_learner.optimised", accuracy=best_accuracy, weights=best_weights, samples=len(parsed))
    write_task_status(
        _TASK, "done", msg,
        started_at=started, finished_at=now_iso(),
        summary={"accuracy": best_accuracy, "weights": best_weights, "samples": len(parsed)},
    )
    return {"status": "ok", "weights": best_weights, "accuracy": best_accuracy}
