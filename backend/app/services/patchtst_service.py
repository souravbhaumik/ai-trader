"""PatchTST price forecasting service.

Replaces the basic Transformer (misnamed "TFT") with PatchTST — a
channel-independent patching Transformer for time-series forecasting
(Nie et al., 2023 — ICLR).

Key advantages over the basic Transformer:
  - Patch tokenization reduces computation from O(L²) to O((L/P)²)
  - Channel independence prevents cross-feature overfitting
  - Instance normalization (RevIN) handles distribution shift
  - Better long-horizon forecasting with fewer parameters

Artifact format:
    {
        "version":          str,
        "config":           {
            "input_size":       int,   # features per timestep
            "d_model":          int,   # model dimension
            "nhead":            int,   # attention heads
            "num_layers":       int,   # encoder layers
            "seq_len":          int,   # input sequence length
            "forecast_horizon": int,   # output prediction horizon
            "patch_len":        int,   # patch length
            "stride":           int,   # patch stride
        },
        "state_dict":       dict,
        "scaler_mean":      list[float],
        "scaler_std":       list[float],
        "feature_names":    list[str],
        "metrics":          dict,
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

_MODEL_PATH = Path(os.getenv("PATCHTST_MODEL_PATH", "/app/models/patchtst/latest.pt"))
_RELOAD_INTERVAL = 300
_SEQ_LEN = 60
_PATCH_LEN = 12
_STRIDE = 8


def _build_model(config: dict):
    """Build PatchTST model from config dict."""
    import torch  # noqa: PLC0415
    import torch.nn as nn  # noqa: PLC0415

    class RevIN(nn.Module):
        """Reversible Instance Normalization for distribution shift."""
        def __init__(self, num_features: int, eps: float = 1e-5):
            super().__init__()
            self.eps = eps
            self.affine_weight = nn.Parameter(torch.ones(num_features))
            self.affine_bias = nn.Parameter(torch.zeros(num_features))

        def forward(self, x, mode: str = "norm"):
            if mode == "norm":
                self._mean = x.mean(dim=1, keepdim=True).detach()
                self._std = (x.std(dim=1, keepdim=True) + self.eps).detach()
                x = (x - self._mean) / self._std
                x = x * self.affine_weight + self.affine_bias
                return x
            else:  # denorm
                x = (x - self.affine_bias) / (self.affine_weight + self.eps)
                x = x * self._std + self._mean
                return x

    class PatchEmbedding(nn.Module):
        """Convert time series into patches and embed."""
        def __init__(self, d_model: int, patch_len: int, stride: int, seq_len: int):
            super().__init__()
            self.patch_len = patch_len
            self.stride = stride
            self.n_patches = (seq_len - patch_len) // stride + 1
            self.proj = nn.Linear(patch_len, d_model)
            self.pos_embed = nn.Parameter(torch.randn(1, self.n_patches, d_model) * 0.02)

        def forward(self, x):
            # x: (batch, seq_len) — single channel
            patches = x.unfold(dimension=1, size=self.patch_len, step=self.stride)
            # patches: (batch, n_patches, patch_len)
            return self.proj(patches) + self.pos_embed

    class PatchTST(nn.Module):
        def __init__(
            self,
            input_size: int,
            d_model: int,
            nhead: int,
            num_layers: int,
            seq_len: int,
            forecast_horizon: int,
            patch_len: int,
            stride: int,
        ):
            super().__init__()
            self.input_size = input_size
            self.forecast_horizon = forecast_horizon
            self.revin = RevIN(seq_len)

            # Channel-independent: one shared patch embedding + transformer
            self.patch_embed = PatchEmbedding(d_model, patch_len, stride, seq_len)
            n_patches = self.patch_embed.n_patches

            enc_layer = nn.TransformerEncoderLayer(
                d_model=d_model,
                nhead=nhead,
                dim_feedforward=d_model * 4,
                dropout=0.1,
                batch_first=True,
            )
            self.transformer = nn.TransformerEncoder(enc_layer, num_layers=num_layers)
            self.head = nn.Linear(n_patches * d_model, forecast_horizon)

        def forward(self, x):
            # x: (batch, seq_len, input_size)
            batch_size = x.shape[0]
            preds = []

            for c in range(self.input_size):
                # Channel independent processing
                xc = x[:, :, c]  # (batch, seq_len)
                xc = self.revin(xc, mode="norm")
                h = self.patch_embed(xc)  # (batch, n_patches, d_model)
                h = self.transformer(h)   # (batch, n_patches, d_model)
                h = h.reshape(batch_size, -1)  # flatten
                out = self.head(h)  # (batch, forecast_horizon)
                out = self.revin(out.unsqueeze(-1).expand(-1, -1, x.shape[1])[:, :, :1]
                                 .squeeze(-1), mode="denorm")[:, :self.forecast_horizon]
                preds.append(out)

            # Average across channels (log-return forecast)
            return torch.stack(preds, dim=-1).mean(dim=-1)  # (batch, forecast_horizon)

    return PatchTST(
        input_size=config["input_size"],
        d_model=config["d_model"],
        nhead=config["nhead"],
        num_layers=config["num_layers"],
        seq_len=config["seq_len"],
        forecast_horizon=config["forecast_horizon"],
        patch_len=config.get("patch_len", _PATCH_LEN),
        stride=config.get("stride", _STRIDE),
    )


# ── Singleton state ──────────────────────────────────────────────────────────

class _State:
    def __init__(self):
        self.model = None
        self.scaler_mean: Optional[np.ndarray] = None
        self.scaler_std: Optional[np.ndarray] = None
        self.version: Optional[str] = None
        self.config: Optional[dict] = None
        self.loaded_at: float = 0.0


_state = _State()
_lock = threading.Lock()


def _load_model() -> bool:
    if not _MODEL_PATH.exists():
        logger.debug("patchtst_service.artifact_not_found", path=str(_MODEL_PATH))
        return False
    try:
        import torch
        try:
            payload = torch.load(_MODEL_PATH, map_location="cpu", weights_only=True)
        except Exception:
            logger.warning("patchtst_service.unsafe_load",
                           msg="Artifact requires weights_only=False; re-save with safe format")
            payload = torch.load(_MODEL_PATH, map_location="cpu", weights_only=False)  # noqa: S301

        model = _build_model(payload["config"])
        model.load_state_dict(payload["state_dict"])
        model.eval()
        _state.model = model
        _state.scaler_mean = np.array(payload["scaler_mean"], dtype=np.float32)
        _state.scaler_std = np.array(payload["scaler_std"], dtype=np.float32)
        _state.version = payload["version"]
        _state.config = payload["config"]
        _state.loaded_at = time.monotonic()
        logger.info("patchtst_service.loaded", version=_state.version)
        return True
    except Exception as exc:
        logger.error("patchtst_service.load_failed", err=str(exc))
        return False


def _maybe_reload():
    if time.monotonic() - _state.loaded_at > _RELOAD_INTERVAL:
        with _lock:
            if time.monotonic() - _state.loaded_at > _RELOAD_INTERVAL:
                _load_model()
                _state.loaded_at = time.monotonic()


def _build_sequence(bars: list[dict], seq_len: int) -> Optional[np.ndarray]:
    """Convert bar dicts to normalized (seq_len, 5) array.

    Features (same as tft_service for compatibility):
        0  log_ret    — log daily return
        1  high_pct   — (high − close) / close
        2  low_pct    — (low  − close) / close
        3  vol_ratio  — volume / rolling-20-day-mean-volume
        4  rsi_norm   — RSI(14) / 100
    """
    need = seq_len + 20
    if len(bars) < need:
        return None

    closes = np.array([b["close"] for b in bars], dtype=np.float32)
    highs = np.array([b["high"] for b in bars], dtype=np.float32)
    lows = np.array([b["low"] for b in bars], dtype=np.float32)
    volumes = np.array([b["volume"] for b in bars], dtype=np.float32)

    log_ret = np.diff(np.log(closes + 1e-8))
    high_pct = (highs[1:] - closes[1:]) / (closes[1:] + 1e-8)
    low_pct = (lows[1:] - closes[1:]) / (closes[1:] + 1e-8)

    n = len(volumes)
    vol_ma20 = np.array([
        volumes[max(0, i - 20):i].mean() if i > 0 else volumes[0]
        for i in range(1, n)
    ], dtype=np.float32)
    vol_ratio = volumes[1:] / (vol_ma20 + 1e-8)

    # RSI series
    rsi = np.full(len(log_ret), 50.0, dtype=np.float32)
    period = 14
    if len(log_ret) >= period + 1:
        gains = np.where(log_ret > 0, log_ret, 0.0)
        losses = np.where(log_ret < 0, -log_ret, 0.0)
        ag = gains[:period].mean()
        al = losses[:period].mean()
        for i in range(period, len(log_ret)):
            ag = (ag * (period - 1) + gains[i]) / period
            al = (al * (period - 1) + losses[i]) / period
            rsi[i] = 100.0 - 100.0 / (1.0 + ag / (al + 1e-8))
    rsi_norm = rsi / 100.0

    features = np.stack([log_ret, high_pct, low_pct, vol_ratio, rsi_norm], axis=1)
    seq = features[-seq_len:].astype(np.float32)

    if _state.scaler_mean is not None:
        seq = (seq - _state.scaler_mean) / (_state.scaler_std + 1e-8)

    return seq


def forecast(symbol: str, bars: list[dict]) -> Optional[dict]:
    """Generate a 5-day PatchTST forecast for a symbol.

    Returns:
        {
            "forecast": [float, ...],  # 5-day predicted prices
            "base_price": float,
            "model_version": str,
        }
    """
    _maybe_reload()

    if _state.model is None:
        with _lock:
            if _state.model is None:
                _load_model()
    if _state.model is None:
        return None

    seq_len = _state.config.get("seq_len", _SEQ_LEN)
    seq = _build_sequence(bars, seq_len)
    if seq is None:
        return None

    import torch
    with torch.no_grad():
        x = torch.from_numpy(seq).unsqueeze(0)  # (1, seq_len, features)
        log_returns = _state.model(x).squeeze(0).numpy()  # (forecast_horizon,)

    base_price = bars[-1]["close"]
    forecast_prices = []
    p = base_price
    for lr in log_returns:
        p = p * np.exp(lr)
        forecast_prices.append(round(float(p), 2))

    return {
        "forecast": forecast_prices,
        "base_price": round(base_price, 2),
        "model_version": _state.version or "patchtst-v1",
    }


def warm_up() -> bool:
    """Pre-load the model."""
    return _load_model()
