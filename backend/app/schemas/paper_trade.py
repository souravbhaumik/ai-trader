"""Pydantic schemas for paper trading endpoints."""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, field_validator


class PaperOrderCreate(BaseModel):
    """Body for manually placing a paper trade."""
    symbol: str
    direction: str          # BUY | SELL
    qty: int
    entry_price: Decimal
    target_price: Optional[Decimal] = None
    stop_loss: Optional[Decimal] = None
    notes: Optional[str] = None

    @field_validator("direction")
    @classmethod
    def _direction_upper(cls, v: str) -> str:
        v = v.upper()
        if v not in {"BUY", "SELL"}:
            raise ValueError("direction must be BUY or SELL")
        return v

    @field_validator("qty")
    @classmethod
    def _qty_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("qty must be positive")
        return v


class PaperOrderClose(BaseModel):
    """Optional body for closing a paper trade; exit_price defaults to current market."""
    exit_price: Optional[Decimal] = None
    status: str = "closed"          # closed | sl_hit | target_hit


class PaperTradeOut(BaseModel):
    """Response schema for a single paper trade."""
    id: uuid.UUID
    user_id: uuid.UUID
    symbol: str
    direction: str
    qty: int
    entry_price: Decimal
    target_price: Optional[Decimal]
    stop_loss: Optional[Decimal]
    exit_price: Optional[Decimal]
    signal_id: Optional[uuid.UUID]
    status: str
    pnl: Optional[Decimal]
    pnl_pct: Optional[Decimal]
    entry_at: datetime
    exit_at: Optional[datetime]
    notes: Optional[str]

    model_config = {"from_attributes": True}


class PortfolioSummary(BaseModel):
    """Aggregated paper portfolio statistics."""
    cash_balance: Decimal
    open_positions: int
    open_value: Decimal          # sum(qty * entry_price) for open trades
    realized_pnl: Decimal
    total_trades: int
    closed_trades: int
    win_rate: Optional[float]    # % closed trades with pnl > 0; None if no closed trades
