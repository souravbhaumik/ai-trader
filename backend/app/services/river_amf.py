"""River Adaptive Random Forest — online ML signal model.

Replaces batch LightGBM with an incrementally-trained Adaptive Random
Forest (ARF) from the River library.  ARF handles concept drift natively
via adaptive windowing on each tree.

Advantages over batch LightGBM:
  - No retrain window needed — learns from every new bar
  - Built-in concept drift handling (ADWIN per tree)
  - No train/test leakage risk (online learning = temporal by design)
  - Lightweight — no need to store/load large model files

Usage:
    from app.services.river_amf import RiverAMFModel

    model = RiverAMFModel()
    model.learn_one(features, label)
    pred = model.predict_one(features)
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

import structlog

logger = structlog.get_logger(__name__)

_MODEL_DIR = Path(os.getenv("MODEL_DIR", "/app/models/river"))
_PERSIST_INTERVAL = 300  # seconds — auto-persist every 5 min


class RiverAMFModel:
    """Thread-safe wrapper around River's AdaptiveRandomForestClassifier."""

    def __init__(
        self,
        n_models: int = 25,
        max_depth: int = 12,
        seed: int = 42,
    ):
        from river import forest, metrics  # type: ignore

        self._model = forest.ARFClassifier(
            n_models=n_models,
            max_depth=max_depth,
            seed=seed,
        )
        self._metrics = {
            "accuracy": metrics.Accuracy(),
            "f1": metrics.F1(),
            "auc": metrics.ROCAUC(),
        }
        self._n_samples = 0
        self._lock = threading.Lock()
        self._last_persist = time.monotonic()
        self._version = "river-arf-v1"

    def learn_one(self, features: Dict[str, float], label: int) -> None:
        """Incrementally learn from a single sample.

        Args:
            features: dict of feature_name → value
            label: 1 (BUY) or 0 (SELL/HOLD)
        """
        with self._lock:
            # Update metrics before learning (test-then-train protocol)
            pred = self._model.predict_proba_one(features)
            if pred:
                for metric in self._metrics.values():
                    metric.update(label, pred)

            self._model.learn_one(features, label)
            self._n_samples += 1

            # Auto-persist periodically
            if time.monotonic() - self._last_persist > _PERSIST_INTERVAL:
                self._persist()

    def predict_one(self, features: Dict[str, float]) -> Optional[Dict[str, Any]]:
        """Predict signal direction and probability for a single feature vector.

        Returns:
            {
                "direction": "BUY" | "SELL" | "HOLD",
                "probability": float,
                "version": str,
                "n_samples": int,
            }
        """
        if self._n_samples < 50:
            return None  # Not enough training data yet

        with self._lock:
            proba = self._model.predict_proba_one(features)

        if not proba:
            return None

        buy_prob = proba.get(1, 0.0)

        if buy_prob >= 0.60:
            direction = "BUY"
        elif buy_prob <= 0.40:
            direction = "SELL"
        else:
            direction = "HOLD"

        return {
            "direction": direction,
            "probability": round(buy_prob, 4),
            "version": self._version,
            "n_samples": self._n_samples,
        }

    def get_metrics(self) -> Dict[str, float]:
        """Return current online evaluation metrics."""
        with self._lock:
            return {
                name: round(float(metric.get()), 4)
                for name, metric in self._metrics.items()
            }

    def _persist(self) -> None:
        """Save model state to disk using pickle."""
        import pickle
        _MODEL_DIR.mkdir(parents=True, exist_ok=True)
        path = _MODEL_DIR / "arf_latest.pkl"
        try:
            with open(path, "wb") as f:
                pickle.dump({
                    "model": self._model,
                    "metrics": {k: v for k, v in self._metrics.items()},
                    "n_samples": self._n_samples,
                    "version": self._version,
                }, f)
            self._last_persist = time.monotonic()
            logger.debug("river_amf.persisted", path=str(path), n_samples=self._n_samples)
        except Exception as exc:
            logger.warning("river_amf.persist_failed", err=str(exc))

    def load(self) -> bool:
        """Load persisted model state from disk."""
        import pickle
        path = _MODEL_DIR / "arf_latest.pkl"
        if not path.exists():
            return False
        try:
            with open(path, "rb") as f:
                data = pickle.load(f)  # noqa: S301
            self._model = data["model"]
            self._metrics = data.get("metrics", self._metrics)
            self._n_samples = data.get("n_samples", 0)
            self._version = data.get("version", self._version)
            logger.info("river_amf.loaded", n_samples=self._n_samples)
            return True
        except Exception as exc:
            logger.warning("river_amf.load_failed", err=str(exc))
            return False

    def force_persist(self) -> None:
        """Explicitly persist model now."""
        with self._lock:
            self._persist()


# ── Module-level singleton ────────────────────────────────────────────────────

_instance: Optional[RiverAMFModel] = None
_instance_lock = threading.Lock()


def get_model() -> RiverAMFModel:
    """Get or create the singleton River AMF model."""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = RiverAMFModel()
                _instance.load()  # Try to restore from disk
    return _instance
