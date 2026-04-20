"""MLflow experiment tracking integration.

Wraps MLflow client for tracking model training runs, logging metrics,
parameters, and artifacts. Configured to use the MLflow server running
as a Docker container.

Usage:
    from app.services.mlflow_service import log_training_run

    log_training_run(
        model_type="river_arf",
        params={"n_models": 25},
        metrics={"accuracy": 0.82, "f1": 0.78},
        artifact_path="/app/models/river/arf_latest.pkl",
    )
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

import structlog

logger = structlog.get_logger(__name__)

_TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI", "http://mlflow:5000")
_EXPERIMENT_NAME = "ai-trader"


def _get_client():
    """Get MLflow client, lazy import."""
    import mlflow  # type: ignore
    mlflow.set_tracking_uri(_TRACKING_URI)
    return mlflow


def log_training_run(
    model_type: str,
    params: Dict[str, Any],
    metrics: Dict[str, float],
    artifact_path: Optional[str] = None,
    tags: Optional[Dict[str, str]] = None,
) -> Optional[str]:
    """Log a model training run to MLflow.

    Returns the MLflow run ID, or None if MLflow is unavailable.
    """
    try:
        mlflow = _get_client()
        mlflow.set_experiment(_EXPERIMENT_NAME)

        with mlflow.start_run(run_name=f"{model_type}-training") as run:
            mlflow.log_params(params)
            mlflow.log_metrics(metrics)

            if tags:
                mlflow.set_tags(tags)

            mlflow.set_tag("model_type", model_type)

            if artifact_path and os.path.exists(artifact_path):
                mlflow.log_artifact(artifact_path)

            run_id = run.info.run_id
            logger.info("mlflow.run_logged",
                        run_id=run_id, model_type=model_type,
                        metrics=metrics)
            return run_id

    except Exception as exc:
        logger.warning("mlflow.log_failed", err=str(exc))
        return None


def log_metric(key: str, value: float, step: Optional[int] = None) -> None:
    """Log a single metric to the active MLflow run."""
    try:
        mlflow = _get_client()
        mlflow.log_metric(key, value, step=step)
    except Exception as exc:
        logger.debug("mlflow.metric_log_failed", key=key, err=str(exc))


def get_best_run(
    model_type: str,
    metric: str = "val_auc",
) -> Optional[Dict[str, Any]]:
    """Find the best run for a model type by a specific metric.

    Returns dict with run_id, params, metrics, or None.
    """
    try:
        mlflow = _get_client()
        experiment = mlflow.get_experiment_by_name(_EXPERIMENT_NAME)
        if experiment is None:
            return None

        runs = mlflow.search_runs(
            experiment_ids=[experiment.experiment_id],
            filter_string=f"tags.model_type = '{model_type}'",
            order_by=[f"metrics.{metric} DESC"],
            max_results=1,
        )

        if runs.empty:
            return None

        best = runs.iloc[0]
        return {
            "run_id": best["run_id"],
            "metrics": {
                k.replace("metrics.", ""): v
                for k, v in best.items()
                if k.startswith("metrics.")
            },
            "params": {
                k.replace("params.", ""): v
                for k, v in best.items()
                if k.startswith("params.")
            },
        }
    except Exception as exc:
        logger.warning("mlflow.search_failed", err=str(exc))
        return None
