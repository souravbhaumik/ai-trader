"""Temporal Fusion Transformer (TFT) price forecasting service — Phase 5.

Loads a pre-trained TFT-inspired Transformer artifact and generates
multi-step (5-day) price forecasts for any symbol.

Architecture
------------
Multi-head self-attention Transformer encoder on a 60-day OHLCV feature
sequence with sinusoidal positional encoding, followed by a linear forecast
head that outputs the next ``forecast_horizon`` daily log-returns.

Artifact format (saved by colab/train_tft_forecaster.ipynb):
    {
        "version":          str,
        "config":           {
                                "input_size":       int,   # 5
                                "d_model":          int,   # 64
                                "nhead":            int,   # 4
                                "num_layers":       int,   # 2
                                "seq_len":          int,   # 60
                                "forecast_horizon": int,   # 5
                            },
        "state_dict":       dict,          # PyTorch state_dict
        "scaler_mean":      list[float],   # per-feature mean from training data
        "scaler_std":       list[float],   # per-feature std  from training data
        "feature_names":    list[str],     # ["log_ret","high_pct","low_pct",
                                           #   "vol_ratio","rsi_norm"]
        "metrics":          dict,          # optional training metrics
    }
"""
from __future__ import annotations

import math
import os
import threading
import time
from pathlib import Path
from typing import Optional

import numpy as np
import structlog

logger = structlog.get_logger(__name__)

_MODEL_PATH      = Path(os.getenv("TFT_MODEL_PATH", "/app/models/tft/latest.pt"))
_RELOAD_INTERVAL = 300   # seconds
_SEQ_LEN         = 60    # must match training config


# ═══════════════════════════════════════════════════════════════════════════════
#  Model definition  (must be identical to colab/train_tft_forecaster.ipynb)
# ═══════════════════════════════════════════════════════════════════════════════

def _build_model(config: dict):
    import torch                    # noqa: PLC0415
    import torch.nn as nn           # noqa: PLC0415

    class PositionalEncoding(nn.Module):
        def __init__(self, d_model: int, max_len: int = 500):
            super().__init__()
            pe  = torch.zeros(max_len, d_model)
            pos = torch.arange(0, max_len).unsqueeze(1).float()
            div = torch.exp(
                torch.arange(0, d_model, 2).float()
                * (-math.log(10_000.0) / d_model)
            )
            pe[:, 0::2] = torch.sin(pos * div)
            pe[:, 1::2] = torch.cos(pos * div)
            self.register_buffer("pe", pe.unsqueeze(0))   # (1, max_len, d_model)

        def forward(self, x):
            return x + self.pe[:, :x.size(1)]

    class TFTForecaster(nn.Module):
        def __init__(
            self,
            input_size:       int,
            d_model:          int,
            nhead:            int,
            num_layers:       int,
            forecast_horizon: int,
        ):
            super().__init__()
            self.input_proj = nn.Linear(input_size, d_model)
            self.pos_enc    = PositionalEncoding(d_model)
            enc_layer       = nn.TransformerEncoderLayer(
                d_model=d_model,
                nhead=nhead,
                dim_feedforward=d_model * 4,
                dropout=0.1,
                batch_first=True,
            )
            self.transformer    = nn.TransformerEncoder(enc_layer, num_layers=num_layers)
            self.forecast_head  = nn.Linear(d_model, forecast_horizon)

        def forward(self, x):
            # x: (batch, seq_len, input_size)
            h = self.input_proj(x)
            h = self.pos_enc(h)
            h = self.transformer(h)
            return self.forecast_head(h[:, -1, :])   # (batch, forecast_horizon)

    return TFTForecaster(
        input_size       = config["input_size"],
        d_model          = config["d_model"],
        nhead            = config["nhead"],
        num_layers       = config["num_layers"],
        forecast_horizon = config["forecast_horizon"],
    )


# ═══════════════════════════════════════════════════════════════════════════════
#  Singleton state + loader
# ═══════════════════════════════════════════════════════════════════════════════

class _State:
    def __init__(self):
        self.model:        object                   = None
        self.scaler_mean:  Optional[np.ndarray]     = None
        self.scaler_std:   Optional[np.ndarray]     = None
        self.version:      Optional[str]            = None
        self.config:       Optional[dict]           = None
        self.loaded_at:    float                    = 0.0


_state = _State()
_lock  = threading.Lock()


def _load_model() -> bool:
    if not _MODEL_PATH.exists():
        logger.debug("tft_service.artifact_not_found", path=str(_MODEL_PATH))
        return False
    try:
        import torch  # noqa: PLC0415
        payload = torch.load(_MODEL_PATH, map_location="cpu", weights_only=False)
        model   = _build_model(payload["config"])
        model.load_state_dict(payload["state_dict"])
        model.eval()
        _state.model       = model
        _state.scaler_mean = np.array(payload["scaler_mean"], dtype=np.float32)
        _state.scaler_std  = np.array(payload["scaler_std"],  dtype=np.float32)
        _state.version     = payload["version"]
        _state.config      = payload["config"]
        _state.loaded_at   = time.monotonic()
        logger.info("tft_service.loaded", version=_state.version)
        return True
    except Exception as exc:
        logger.error("tft_service.load_failed", error=str(exc))
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

def _rsi_series(log_rets: np.ndarray, period: int = 14) -> np.ndarray:
    """Compute RSI series from log-return array, same length as input."""
    n   = len(log_rets)
    out = np.full(n, 50.0, dtype=np.float32)
    if n < period + 1:
        return out
    gains  = np.where(log_rets > 0, log_rets, 0.0)
    losses = np.where(log_rets < 0, -log_rets, 0.0)
    ag     = gains[:period].mean()
    al     = losses[:period].mean()
    for i in range(period, n):
        ag       = (ag * (period - 1) + gains[i])  / period
        al       = (al * (period - 1) + losses[i]) / period
        out[i]   = 100.0 - 100.0 / (1.0 + ag / (al + 1e-8))
    return out


def _build_sequence(bars: list[dict], seq_len: int) -> Optional[np.ndarray]:
    """Convert bar dicts → normalised (seq_len, 5) float32 array.

    Features (must match notebook):
        0  log_ret    — log daily return
        1  high_pct   — (high − close) / close
        2  low_pct    — (low  − close) / close
        3  vol_ratio  — volume / rolling-20-day-mean-volume
        4  rsi_norm   — RSI(14) / 100

    bars must be sorted oldest → newest; need ≥ seq_len + 20.
    """
    need = seq_len + 20
    if len(bars) < need:
        return None

    closes  = np.array([b["close"]  for b in bars], dtype=np.float32)
    highs   = np.array([b["high"]   for b in bars], dtype=np.float32)
    lows    = np.array([b["low"]    for b in bars], dtype=np.float32)
    volumes = np.array([b["volume"] for b in bars], dtype=np.float32)

    log_ret  = np.diff(np.log(closes + 1e-8))
    high_pct = (highs[1:] - closes[1:]) / (closes[1:] + 1e-8)
    low_pct  = (lows[1:]  - closes[1:]) / (closes[1:] + 1e-8)

    n = len(volumes)
    vol_ma = np.array([
        volumes[max(0, i - 20):i].mean() if i > 0 else volumes[0]
        for i in range(1, n)
    ], dtype=np.float32)
    vol_ratio = volumes[1:] / (vol_ma + 1e-8)

    rsi_norm  = _rsi_series(log_ret) / 100.0

    features = np.stack([log_ret, high_pct, low_pct, vol_ratio, rsi_norm], axis=1)
    seq      = features[-seq_len:].astype(np.float32)

    if _state.scaler_mean is not None:
        seq = (seq - _state.scaler_mean) / (_state.scaler_std + 1e-8)

    return seq


# ═══════════════════════════════════════════════════════════════════════════════
#  Public API
# ═══════════════════════════════════════════════════════════════════════════════

def forecast(
    symbol: str,
    bars:   list[dict],
) -> Optional[dict]:
    """Generate a multi-step price forecast.

    Args:
        symbol: ticker string (used only for logging)
        bars:   list of dicts with keys ``close, high, low, volume``
                sorted oldest → newest; need ≥ 80 bars.

    Returns::

        {
            "prices":         [float, ...],  # absolute price predictions (5 values)
            "returns":        [float, ...],  # predicted daily log-returns
            "horizon_days":   5,
            "base_price":     float,         # last known close
            "version":        str,
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

    seq_len = _state.config["seq_len"]
    seq     = _build_sequence(bars, seq_len)
    if seq is None:
        return None

    try:
        import torch  # noqa: PLC0415
        with torch.no_grad():
            x            = torch.tensor(seq).unsqueeze(0)   # (1, seq_len, 5)
            pred_returns = _state.model(x)[0].numpy()       # (horizon,)

        base_price = float(bars[-1]["close"])
        prices: list[float] = []
        p = base_price
        for r in pred_returns:
            p = float(p * np.exp(float(r)))
            prices.append(round(p, 2))

        return {
            "prices":       prices,
            "returns":      [round(float(r), 6) for r in pred_returns],
            "horizon_days": len(prices),
            "base_price":   round(base_price, 2),
            "version":      _state.version,
        }
    except Exception as exc:
        logger.warning("tft_service.forecast_failed", symbol=symbol, error=str(exc))
        return None


def is_available() -> bool:
    """Return True if a TFT model is loaded and ready for inference."""
    _maybe_reload()
    return _state.model is not None


def warm_up() -> bool:
    """Eagerly load the TFT model into memory.

    Call this once at Celery worker startup so the first ``forecast`` call does
    not pay the cold-load penalty during a live signal generation run.
    Returns True if the model loaded successfully, False if the artifact is absent.
    """
    with _lock:
        return _load_model()
