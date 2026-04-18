"""Forecast API — Phase 5.

Endpoints
---------
GET  /api/v1/forecasts/{symbol}                5-day TFT price forecast
GET  /api/v1/forecasts/{symbol}/anomaly        LSTM anomaly score
POST /api/v1/admin/models/download-from-drive  Trigger GDrive model download (admin only)
GET  /api/v1/admin/models/deep-learning        List LSTM + TFT model versions
"""
from __future__ import annotations

import asyncio
import sys
from typing import Any, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from jose import JWTError
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_session
from app.core.security import decode_access_token

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["forecasts"])


# ══════════════════════════════════════════════════════════════════════════════
#  Auth helpers
# ══════════════════════════════════════════════════════════════════════════════

async def _get_current_user(request: Request) -> dict:
    auth_hdr = request.headers.get("Authorization", "")
    if not auth_hdr.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token.")
    token = auth_hdr.removeprefix("Bearer ").strip()
    try:
        return decode_access_token(token)
    except JWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token.")


async def _require_admin(request: Request) -> dict:
    payload = await _get_current_user(request)
    if payload.get("role") != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin role required.")
    return payload


# ══════════════════════════════════════════════════════════════════════════════
#  OHLCV helper
# ══════════════════════════════════════════════════════════════════════════════

async def _fetch_bars(
    symbol: str,
    session: AsyncSession,
    limit: int = 120,
) -> list[dict]:
    # Strip exchange suffixes (.NS, .BO, .BSE) — DB stores bare ticker symbols
    clean = symbol.upper().split(".")[0]
    rows = (
        await session.execute(
            text("""
                SELECT close, high, low, volume
                FROM   ohlcv_daily
                WHERE  symbol = :symbol
                ORDER  BY ts ASC
                LIMIT  :limit
            """),
            {"symbol": clean, "limit": limit},
        )
    ).fetchall()
    return [
        {
            "close":  float(r[0]),
            "high":   float(r[1]),
            "low":    float(r[2]),
            "volume": float(r[3]),
        }
        for r in rows
    ]


# ══════════════════════════════════════════════════════════════════════════════
#  Schemas
# ══════════════════════════════════════════════════════════════════════════════

class ForecastResponse(BaseModel):
    symbol:       str
    prices:       list[float]
    returns:      list[float]
    base_price:   float
    horizon_days: int
    version:      str


class AnomalyResponse(BaseModel):
    symbol:     str
    score:      float    # MSE / threshold  (>1.0 = anomalous)
    mse:        float
    threshold:  float
    is_anomaly: bool
    version:    str


class DownloadResponse(BaseModel):
    lstm_ok:  bool
    tft_ok:   bool
    message:  str


class DLModelRow(BaseModel):
    id:           str
    model_type:   str
    version:      str
    is_active:    bool
    trained_at:   str
    promoted_at:  Optional[str]
    metrics:      Any


# ══════════════════════════════════════════════════════════════════════════════
#  Forecast endpoints
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/forecasts/{symbol}", response_model=ForecastResponse)
async def get_forecast(
    symbol:  str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """5-day ahead TFT price forecast for a symbol.

    Requires an active TFT model (see ``POST /admin/models/download-from-drive``).
    """
    await _get_current_user(request)

    bars = await _fetch_bars(symbol, session, limit=120)
    if len(bars) < 82:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Insufficient price history for {symbol!r} (have {len(bars)} bars, need ≥82).",
        )

    from app.services.tft_service import forecast  # noqa: PLC0415

    result = forecast(symbol, bars)
    if result is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "TFT forecast model is not loaded. "
            "Train it on Colab (colab/train_tft_forecaster.ipynb) then run: "
            "docker compose exec backend python scripts/download_models.py",
        )

    return ForecastResponse(symbol=symbol.upper(), **result)


@router.get("/forecasts/{symbol}/anomaly", response_model=AnomalyResponse)
async def get_anomaly(
    symbol:  str,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """LSTM reconstruction-error anomaly score for a symbol.

    score > 1.0  indicates unusual market behaviour for the symbol.
    """
    await _get_current_user(request)

    bars = await _fetch_bars(symbol, session, limit=80)
    if len(bars) < 51:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Insufficient price history for {symbol!r} (have {len(bars)} bars, need ≥51).",
        )

    from app.services.lstm_service import compute_anomaly_score  # noqa: PLC0415

    result = compute_anomaly_score(symbol, bars)
    if result is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "LSTM anomaly model is not loaded. "
            "Train it on Colab (colab/train_lstm_autoencoder.ipynb) then run: "
            "docker compose exec backend python scripts/download_models.py",
        )

    return AnomalyResponse(symbol=symbol.upper(), **result)


# ══════════════════════════════════════════════════════════════════════════════
#  Admin endpoints
# ══════════════════════════════════════════════════════════════════════════════

@router.post("/admin/models/download-from-drive", response_model=DownloadResponse)
async def download_models_from_drive(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """Trigger LSTM + TFT model download from Google Drive (admin only).

    Requires ``LSTM_GDRIVE_ID`` and/or ``TFT_GDRIVE_ID`` environment variables.
    """
    await _require_admin(request)

    proc = await asyncio.create_subprocess_exec(
        sys.executable, "/app/scripts/download_models.py",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    output = (stdout + stderr).decode(errors="replace")

    lower    = output.lower()
    lstm_ok  = "lstm" in lower and ("done" in lower or "register.done" in lower)
    tft_ok   = "tft"  in lower and ("done" in lower or "register.done" in lower)

    logger.info(
        "admin.download_models",
        returncode=proc.returncode,
        lstm_ok=lstm_ok,
        tft_ok=tft_ok,
    )
    return DownloadResponse(
        lstm_ok=lstm_ok,
        tft_ok=tft_ok,
        message=output[:2000].strip(),
    )


@router.get("/admin/models/deep-learning", response_model=list[DLModelRow])
async def list_dl_models(
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    """List all LSTM and TFT model versions registered in ml_models (admin only)."""
    await _require_admin(request)

    rows = (
        await session.execute(
            text("""
                SELECT id, model_type, version, is_active,
                       trained_at, promoted_at, metrics
                FROM   ml_models
                WHERE  model_type IN ('lstm', 'tft')
                ORDER  BY trained_at DESC
                LIMIT  50
            """)
        )
    ).fetchall()

    return [
        DLModelRow(
            id          = str(r[0]),
            model_type  = r[1],
            version     = r[2],
            is_active   = r[3],
            trained_at  = r[4].isoformat() if r[4] else "",
            promoted_at = r[5].isoformat() if r[5] else None,
            metrics     = r[6] or {},
        )
        for r in rows
    ]
