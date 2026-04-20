"""ML training Celery task — Phase 3.

Triggered manually by admin or on a weekly schedule.
Runs LightGBM training and optionally logs to MLflow.

Usage:
    # Via admin API
    POST /api/v1/admin/pipeline/train-model

    # Manual trigger
    docker compose exec celery-worker sh -c "PYTHONPATH=/app python -c \
      'from app.tasks.ml_training import train_model; print(train_model())'"
"""
from __future__ import annotations

import os
from typing import Any

import structlog

from app.tasks.celery_app import celery_app
from app.tasks.task_utils import (
    append_task_log, clear_task_logs, now_iso, write_task_status,
)

logger = structlog.get_logger(__name__)

_TASK                = "ml_training"
_MLFLOW_TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI", "")
_MLFLOW_EXPERIMENT   = os.getenv("MLFLOW_EXPERIMENT", "ai-trader-lgbm")


@celery_app.task(name="app.tasks.ml_training.train_model", bind=True)
def train_model(self, **kwargs) -> dict[str, Any]:
    """Train LightGBM, log to MLflow if configured, register in ml_models."""
    from app.services.lgbm_trainer import train_lgbm

    started = now_iso()
    clear_task_logs(_TASK)
    write_task_status(_TASK, "running", "LightGBM training started…", started_at=started)
    logger.info("ml_training.start")

    try:
        result = train_lgbm(**kwargs)
    except Exception as exc:
        msg = f"Training failed: {exc}"
        logger.error("ml_training.failed", err=str(exc))
        write_task_status(_TASK, "error", msg, started_at=started, finished_at=now_iso())
        raise

    # ── Optional MLflow logging ───────────────────────────────────────────────
    if _MLFLOW_TRACKING_URI:
        append_task_log(_TASK, "Logging run to MLflow…")
        _log_to_mlflow(result)

    metrics_str = ", ".join(f"{k}={v:.4f}" for k, v in result.get("metrics", {}).items())
    msg = f"Training done — version={result.get('version')}. Metrics: {metrics_str}"
    logger.info("ml_training.done", **{k: v for k, v in result.items() if k != "metrics"},
                **result.get("metrics", {}))
    write_task_status(
        _TASK, "done", msg,
        started_at=started, finished_at=now_iso(),
        summary={"version": result.get("version"), "metrics": result.get("metrics", {})},
    )
    return result


def _log_to_mlflow(result: dict) -> None:
    """Log training run to MLflow tracking server if available."""
    try:
        import mlflow

        mlflow.set_tracking_uri(_MLFLOW_TRACKING_URI)
        mlflow.set_experiment(_MLFLOW_EXPERIMENT)

        with mlflow.start_run(run_name=result["version"]):
            mlflow.log_params({
                "version": result["version"],
                "model_id": result["model_id"],
            })
            mlflow.log_metrics(result["metrics"])
            mlflow.log_artifact(result["artifact"])

            # Update mlflow_run_id in ml_models
            from sqlalchemy import text
            from app.core.database import get_sync_session

            run_id = mlflow.active_run().info.run_id
            with get_sync_session() as session:
                session.execute(
                    text("UPDATE ml_models SET mlflow_run_id = :rid WHERE id = :mid"),
                    {"rid": run_id, "mid": result["model_id"]},
                )
                session.commit()

        logger.info("ml_training.mlflow_logged", run_id=run_id)

    except Exception as exc:
        logger.warning("ml_training.mlflow_failed", err=str(exc))
