"""Pydantic schemas for Signal input/output validation."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


class SignalBase(BaseModel):
    symbol: str = Field(..., max_length=32, description="NSE/BSE symbol")
    signal_type: str = Field(..., pattern="^(BUY|SELL|HOLD)$", description="BUY, SELL, or HOLD")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Signal confidence 0-1")
    entry_price: Optional[float] = Field(default=None, ge=0)
    target_price: Optional[float] = Field(default=None, ge=0)
    stop_loss: Optional[float] = Field(default=None, ge=0)
    explanation: Optional[str] = Field(default=None, max_length=1000)
    meta: Optional[Dict[str, Any]] = None


class SignalCreate(SignalBase):
    """Input schema for creating a signal."""
    pass


class SignalOut(SignalBase):
    """Output schema for a single signal."""
    id: str
    ts: datetime
    regime: Optional[str] = Field(default=None, description="Market regime at signal time")
    drift_penalty: Optional[float] = Field(default=None, description="Drift confidence penalty applied")
    model_version: Optional[str] = None

    class Config:
        from_attributes = True


class SignalListOut(BaseModel):
    """Paginated list of signals."""
    items: List[SignalOut]
    total: int
    page: int
    page_size: int


class ForecastOut(BaseModel):
    """PatchTST / LSTM forecast response."""
    symbol: str
    forecast: List[float] = Field(description="Predicted prices for next N days")
    base_price: float
    model_version: str
    regime: Optional[str] = None
