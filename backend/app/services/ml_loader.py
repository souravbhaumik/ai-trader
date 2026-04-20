"""ML model loader — Phase 3.

Provides a thread-safe singleton that loads the currently active LightGBM
model from disk (based on the ``ml_models`` table) and exposes a
``predict(features)`` method used by the signal generator.

The model is reloaded automatically:
  - On first call (lazy init)
  - When the active model version changes in the DB (detected every 5 min)
"""
from __future__ import annotations

import pickle
import threading
import time
from pathlib import Path
from typing import Optional

import structlog

from app.core.database import get_sync_session

logger = structlog.get_logger(__name__)

_RELOAD_INTERVAL = 300  # seconds — how often to check for a new active model


class _ModelState:
    def __init__(self):
        self.model       = None
        self.version     = None
        self.model_id    = None
        self.feature_names: list[str] = []
        self.loaded_at   = 0.0


_state = _ModelState()
_lock  = threading.Lock()


def _load_active_model() -> bool:
    """Query ml_models for the active version and load it from disk.

    Returns True if a model was loaded, False if none is active or load fails.
    """
    from sqlalchemy import text

    try:
        with get_sync_session() as session:
            row = session.execute(
                text("""
                    SELECT id, version, artifact_path, feature_names
                    FROM   ml_models
                    WHERE  model_type = 'lgbm' AND is_active = TRUE
                    ORDER  BY promoted_at DESC NULLS LAST
                    LIMIT  1
                """)
            ).fetchone()

        if row is None:
            logger.warning("ml_loader.no_active_model")
            return False

        model_id, version, artifact_path, feature_names = row

        # Already loaded this version — skip
        if _state.version == version:
            return True

        path = Path(artifact_path)
        if not path.exists():
            logger.error("ml_loader.artifact_missing", path=str(path), version=version)
            return False

        with open(path, "rb") as fh:
            payload = pickle.load(fh)

        _state.model        = payload["model"]
        _state.version      = payload.get("version", version)
        _state.model_id     = str(model_id)
        _state.feature_names = payload.get("feature_names", [])
        _state.loaded_at    = time.monotonic()

        logger.info("ml_loader.loaded", version=_state.version, model_id=_state.model_id)
        return True

    except Exception as exc:
        logger.error("ml_loader.load_failed", err=str(exc))
        return False


def _maybe_reload():
    """Reload model if the TTL has elapsed (non-blocking for callers)."""
    if time.monotonic() - _state.loaded_at > _RELOAD_INTERVAL:
        with _lock:
            if time.monotonic() - _state.loaded_at > _RELOAD_INTERVAL:
                _load_active_model()
                _state.loaded_at = time.monotonic()


def predict(features: dict[str, float]) -> Optional[dict]:
    """Return prediction dict for a single feature vector.

    Returns::

        {
            "direction":   "BUY" | "SELL" | "HOLD",
            "probability": float,   # [0, 1] — probability of BUY class
            "version":     str,
            "model_id":    str,
        }

    Returns ``None`` if no model is loaded.
    """
    _maybe_reload()

    if _state.model is None:
        # Try once more (first call)
        with _lock:
            if _state.model is None:
                _load_active_model()

    if _state.model is None:
        return None

    import numpy as np

    feature_names = _state.feature_names
    import math as _math
    row = [v if not _math.isnan(v := features.get(n, 0.0)) else 0.0 for n in feature_names]
    X   = np.array([row], dtype=np.float32)

    proba = float(_state.model.predict_proba(X)[0, 1])

    if proba >= 0.60:
        direction = "BUY"
    elif proba <= 0.40:
        direction = "SELL"
    else:
        direction = "HOLD"

    return {
        "direction":   direction,
        "probability": round(proba, 4),
        "version":     _state.version,
        "model_id":    _state.model_id,
    }


def force_reload():
    """Force an immediate model reload (called by admin promote endpoint)."""
    with _lock:
        _state.loaded_at = 0.0
        _load_active_model()
