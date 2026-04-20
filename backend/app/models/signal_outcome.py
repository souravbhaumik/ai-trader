"""SignalOutcome SQLModel — maps to the `signal_outcomes` table.

Tracks the actual performance of AI signals after they are generated.
Used for win-rate analytics, model evaluation, and user transparency.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlmodel import Field, SQLModel


class SignalOutcome(SQLModel, table=True):
    __tablename__ = "signal_outcomes"

    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        primary_key=True,
        nullable=False,
    )
    signal_id: uuid.UUID = Field(nullable=False, index=True)
    symbol: str = Field(max_length=32, nullable=False)
    signal_type: str = Field(max_length=10, nullable=False)  # BUY / SELL
    signal_ts: datetime = Field(nullable=False)
    entry_price: Decimal = Field(nullable=False, decimal_places=4, max_digits=14)
    target_price: Optional[Decimal] = Field(default=None, decimal_places=4, max_digits=14)
    stop_loss: Optional[Decimal] = Field(default=None, decimal_places=4, max_digits=14)
    confidence: Decimal = Field(default=Decimal("0"), decimal_places=4, max_digits=5)

    # Price tracking at different intervals
    price_1d: Optional[Decimal] = Field(default=None, decimal_places=4, max_digits=14)
    price_3d: Optional[Decimal] = Field(default=None, decimal_places=4, max_digits=14)
    price_5d: Optional[Decimal] = Field(default=None, decimal_places=4, max_digits=14)

    # Return percentages
    return_1d_pct: Optional[Decimal] = Field(default=None, decimal_places=4, max_digits=8)
    return_3d_pct: Optional[Decimal] = Field(default=None, decimal_places=4, max_digits=8)
    return_5d_pct: Optional[Decimal] = Field(default=None, decimal_places=4, max_digits=8)

    # Target/StopLoss tracking
    hit_target: bool = Field(default=False)
    hit_stoploss: bool = Field(default=False)
    hit_target_at: Optional[datetime] = Field(default=None)
    hit_stoploss_at: Optional[datetime] = Field(default=None)
    max_gain_pct: Optional[Decimal] = Field(default=None, decimal_places=4, max_digits=8)
    max_drawdown_pct: Optional[Decimal] = Field(default=None, decimal_places=4, max_digits=8)

    # Evaluation status
    is_evaluated: bool = Field(default=False)
    evaluated_at: Optional[datetime] = Field(default=None)

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )
    tbl_last_dt: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )
