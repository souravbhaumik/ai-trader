"""LSTM Autoencoder inference service — Phase 5.

Loads a pre-trained LSTM autoencoder artifact (trained on Colab, downloaded
from Google Drive) and computes per-symbol anomaly scores.

High reconstruction error → the market is behaving unusually for that symbol
→ signals are penalised so we don't enter into abnormally volatile / gapped
  conditions.

Artifact format (saved by colab/train_lstm_autoencoder.ipynb):
    {
        "version":        str,
        "config":         {"input_size": 4, "hidden_size": 64,
                           "num_layers": 2, "seq_len": 30},
        "state_dict":     dict,        # PyTorch state_dict
        "scaler_mean":    list[float], # per-feature mean from training data
        "scaler_std":     list[float], # per-feature std  from training data
        "threshold":      float,       # 95th-pct reconstruction MSE on train set
        "feature_names":  list[str],   # ["close_pct","hl_ratio","vol_ratio","close_vs_ma20"]
        "metrics":        dict,        # optional training metrics
    }
"""
from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Optional

import numpy as np
import structlog

logger = structlog.get_logger(__name__)

_MODEL_PATH      = Path(os.getenv("LSTM_MODEL_PATH", "/app/models/lstm/latest.pt"))
_RELOAD_INTERVAL = 300   # seconds — poll for new artifact
_SEQ_LEN         = 30    # rolling window length (must match training config)


# ═══════════════════════════════════════════════════════════════════════════════
#  Model definition  (must be identical to colab/train_lstm_autoencoder.ipynb)
# ═══════════════════════════════════════════════════════════════════════════════

def _build_model(config: dict):
    import torch.nn as nn  # noqa: PLC0415

    class LSTMAutoencoder(nn.Module):
        def __init__(self, input_size: int, hidden_size: int,
                     num_layers: int, seq_len: int):
            super().__init__()
            self.seq_len = seq_len
            drop = 0.1 if num_layers > 1 else 0.0
            self.encoder = nn.LSTM(
                input_size, hidden_size, num_layers,
                batch_first=True, dropout=drop,
            )
            self.decoder = nn.LSTM(
                hidden_size, hidden_size, num_layers,
                batch_first=True, dropout=drop,
            )
            self.output_layer = nn.Linear(hidden_size, input_size)

        def forward(self, x):
            # x : (batch, seq_len, input_size)
            _, (h, _) = self.encoder(x)
            # bottleneck: repeat last hidden state across the time axis
            dec_in = h[-1].unsqueeze(1).repeat(1, self.seq_len, 1)
            dec_out, _ = self.decoder(dec_in)
            return self.output_layer(dec_out)   # (batch, seq_len, input_size)

    return LSTMAutoencoder(
        input_size  = config["input_size"],
        hidden_size = config["hidden_size"],
        num_layers  = config["num_layers"],
        seq_len     = config["seq_len"],
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  Singleton state + loader
# ═══════════════════════════════════════════════════════════════════════════════

class _State:
    def __init__(self):
        self.model                          = None
        self.scaler_mean: Optional[np.ndarray] = None
        self.scaler_std:  Optional[np.ndarray] = None
        self.threshold:   Optional[float]      = None
        self.version:     Optional[str]        = None
        self.config:      Optional[dict]       = None
        self.loaded_at:   float                = 0.0


_state = _State()
_lock  = threading.Lock()


def _load_model() -> bool:
    if not _MODEL_PATH.exists():
        logger.debug("lstm_service.artifact_not_found", path=str(_MODEL_PATH))
        return False
    try:
        import torch  # noqa: PLC0415
        try:
            payload = torch.load(_MODEL_PATH, map_location="cpu", weights_only=True)
        except Exception:
            logger.warning("lstm_service.unsafe_load",
                           msg="Artifact requires weights_only=False; re-save with safe format")
            payload = torch.load(_MODEL_PATH, map_location="cpu", weights_only=False)  # noqa: S301
        model   = _build_model(payload["config"])
        model.load_state_dict(payload["state_dict"])
        model.eval()
        _state.model        = model
        _state.scaler_mean  = np.array(payload["scaler_mean"], dtype=np.float32)
        _state.scaler_std   = np.array(payload["scaler_std"],  dtype=np.float32)
        _state.threshold    = float(payload["threshold"])
        _state.version      = payload["version"]
        _state.config       = payload["config"]
        _state.loaded_at    = time.monotonic()
        logger.info("lstm_service.loaded",
                    version=_state.version, threshold=round(_state.threshold, 6))
        return True
    except Exception as exc:
        logger.error("lstm_service.load_failed", err=str(exc))
        return False


def _maybe_reload() -> None:
    if time.monotonic() - _state.loaded_at > _RELOAD_INTERVAL:
        with _lock:
            if time.monotonic() - _state.loaded_at > _RELOAD_INTERVAL:
                _load_model()
                _state.loaded_at = time.monotonic()


# ═══════════════════════════════════════════════════════════════════════════════
#  Feature builder
# ═══════════════════════════════════════════════════════════════════════════════

def _build_sequence(bars: list[dict]) -> Optional[np.ndarray]:
    """Convert list of bar dicts → normalised (seq_len, 4) float32 array.

    Features (must match notebook):
        0  close_pct      — daily close return
        1  hl_ratio       — (high − low) / close  (intraday range %)
        2  vol_ratio      — volume / rolling-20-day-mean-volume
        3  close_vs_ma20  — (close − SMA20) / SMA20

    bars must be sorted oldest → newest with at least seq_len + 20 entries.
    """
    need = _SEQ_LEN + 20
    if len(bars) < need:
        return None

    closes  = np.array([b["close"]  for b in bars], dtype=np.float32)
    highs   = np.array([b["high"]   for b in bars], dtype=np.float32)
    lows    = np.array([b["low"]    for b in bars], dtype=np.float32)
    volumes = np.array([b["volume"] for b in bars], dtype=np.float32)

    # ── close_pct ─────────────────────────────────────────────────────────────
    close_pct = np.diff(closes) / (closes[:-1] + 1e-8)

    # ── hl_ratio ──────────────────────────────────────────────────────────────
    hl_ratio = (highs[1:] - lows[1:]) / (closes[1:] + 1e-8)

    # ── vol_ratio ─────────────────────────────────────────────────────────────
    n = len(volumes)
    vol_ma20 = np.array([
        volumes[max(0, i - 20):i].mean() if i > 0 else volumes[0]
        for i in range(1, n)
    ], dtype=np.float32)
    vol_ratio = volumes[1:] / (vol_ma20 + 1e-8)

    # ── close_vs_ma20 ─────────────────────────────────────────────────────────
    ma20 = np.array([
        closes[max(0, i - 20):i + 1].mean()
        for i in range(1, len(closes))
    ], dtype=np.float32)
    close_vs_ma20 = (closes[1:] - ma20) / (ma20 + 1e-8)

    # ── stack → (T, 4) ────────────────────────────────────────────────────────
    features = np.stack([close_pct, hl_ratio, vol_ratio, close_vs_ma20], axis=1)
    seq = features[-_SEQ_LEN:].astype(np.float32)   # (seq_len, 4)

    # ── standardise ───────────────────────────────────────────────────────────
    if _state.scaler_mean is not None:
        seq = (seq - _state.scaler_mean) / (_state.scaler_std + 1e-8)

    return seq


# ═══════════════════════════════════════════════════════════════════════════════
#  Public API
# ═══════════════════════════════════════════════════════════════════════════════

def compute_anomaly_score(
    symbol: str,
    bars: list[dict],
) -> Optional[dict]:
    """Compute LSTM reconstruction-error anomaly score for a symbol.

    Args:
        symbol: ticker string (used only for logging)
        bars:   list of dicts with keys ``close, high, low, volume``
                sorted oldest → newest; need ≥ 50 bars.

    Returns::

        {
            "score":      float,  # MSE / threshold  (>1.0 means anomalous)
            "mse":        float,  # raw reconstruction MSE
            "threshold":  float,  # training 95th-pct threshold
            "is_anomaly": bool,
            "version":    str,
        }

    Returns ``None`` if no model is loaded or bars are insufficient.
    """
    _maybe_reload()

    if _state.model is None:
        with _lock:
            if _state.model is None:
                _load_model()
        if _state.model is None:
            return None

    seq = _build_sequence(bars)
    if seq is None:
        return None

    try:
        import torch  # noqa: PLC0415
        with torch.no_grad():
            x     = torch.tensor(seq).unsqueeze(0)   # (1, seq_len, 4)
            x_hat = _state.model(x)
            mse   = float(((x - x_hat) ** 2).mean().item())

        score = mse / (_state.threshold + 1e-8)
        return {
            "score":      round(score, 4),
            "mse":        round(mse,   8),
            "threshold":  round(_state.threshold, 8),
            "is_anomaly": score > 1.0,
            "version":    _state.version,
        }
    except Exception as exc:
        logger.warning("lstm_service.score_failed", symbol=symbol, err=str(exc))
        return None


def is_available() -> bool:
    """Return True if an LSTM model is loaded and ready for inference."""
    _maybe_reload()
    return _state.model is not None


def warm_up() -> bool:
    """Eagerly load the LSTM model into memory.

    Call this once at Celery worker startup so the first ``compute_anomaly_score``
    call does not pay the cold-load penalty during a live signal generation run.
    Returns True if the model loaded successfully, False if the artifact is absent.
    """
    with _lock:
        return _load_model()
