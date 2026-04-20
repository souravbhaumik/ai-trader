"""ADWIN concept drift detector for ML model monitoring.

Uses the ADWIN (Adaptive Windowing) algorithm from River to detect when
the feature distribution has shifted significantly, indicating the model's
training distribution no longer matches production data.

When drift is detected:
  - Log a warning
  - Optionally trigger model retraining
  - Reduce confidence of current predictions

Usage:
    from app.services.drift_detector import DriftDetector

    detector = DriftDetector()
    detector.update("rsi_14", 72.5)
    if detector.drift_detected("rsi_14"):
        logger.warning("RSI distribution has shifted!")
"""
from __future__ import annotations

import threading
from typing import Dict, Optional, Set

import structlog

logger = structlog.get_logger(__name__)


class DriftDetector:
    """Per-feature ADWIN drift detector using River."""

    def __init__(self, delta: float = 0.002):
        """
        Args:
            delta: Confidence parameter for ADWIN. Lower = more sensitive.
                   0.002 is a good default for financial features.
        """
        self._delta = delta
        self._detectors: Dict[str, object] = {}
        self._drifted_features: Set[str] = set()
        self._lock = threading.Lock()
        self._n_updates: Dict[str, int] = {}

    def _get_detector(self, feature_name: str):
        """Lazy-init ADWIN detector for a feature."""
        if feature_name not in self._detectors:
            from river.drift import ADWIN  # type: ignore
            self._detectors[feature_name] = ADWIN(delta=self._delta)
            self._n_updates[feature_name] = 0
        return self._detectors[feature_name]

    def update(self, feature_name: str, value: float) -> bool:
        """Feed a new observation to the drift detector.

        Returns True if drift was just detected on this update.
        """
        with self._lock:
            detector = self._get_detector(feature_name)
            self._n_updates[feature_name] += 1

            detector.update(value)

            if detector.drift_detected:
                if feature_name not in self._drifted_features:
                    self._drifted_features.add(feature_name)
                    logger.warning(
                        "drift_detector.drift_detected",
                        feature=feature_name,
                        n_samples=self._n_updates[feature_name],
                    )
                return True

            # Clear drift flag if detector no longer reports drift
            self._drifted_features.discard(feature_name)
            return False

    def update_batch(self, features: Dict[str, float]) -> Set[str]:
        """Update all features and return set of features with active drift."""
        drifted = set()
        for name, value in features.items():
            if self.update(name, value):
                drifted.add(name)
        return drifted

    def drift_detected(self, feature_name: str) -> bool:
        """Check if drift is currently active for a feature."""
        return feature_name in self._drifted_features

    @property
    def all_drifted(self) -> Set[str]:
        """Return set of all features currently in drift."""
        return set(self._drifted_features)

    @property
    def drift_ratio(self) -> float:
        """Fraction of monitored features that have drifted."""
        if not self._detectors:
            return 0.0
        return len(self._drifted_features) / len(self._detectors)

    def get_confidence_penalty(self) -> float:
        """Calculate a confidence penalty [0, 0.5] based on drift severity."""
        ratio = self.drift_ratio
        if ratio == 0:
            return 0.0
        # Linear penalty: 0% drift → 0 penalty, 50%+ drift → 0.5 penalty
        return min(ratio, 0.5)

    def reset(self, feature_name: Optional[str] = None) -> None:
        """Reset drift detector for a feature or all features."""
        with self._lock:
            if feature_name:
                self._detectors.pop(feature_name, None)
                self._drifted_features.discard(feature_name)
                self._n_updates.pop(feature_name, None)
            else:
                self._detectors.clear()
                self._drifted_features.clear()
                self._n_updates.clear()


# Module-level singleton
_detector: Optional[DriftDetector] = None
_detector_lock = threading.Lock()


def get_drift_detector() -> DriftDetector:
    """Get or create the singleton drift detector."""
    global _detector
    if _detector is None:
        with _detector_lock:
            if _detector is None:
                _detector = DriftDetector()
    return _detector
