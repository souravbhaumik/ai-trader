"""PaperTrade SQLModel — maps to the ``paper_trades`` table."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlmodel import Field, SQLModel


class PaperTrade(SQLModel, table=True):
    __tablename__ = "paper_trades"

    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        primary_key=True,
        nullable=False,
    )
    user_id: uuid.UUID = Field(foreign_key="users.id", nullable=False, index=True)
    symbol: str = Field(max_length=32)
    direction: str = Field(max_length=8)          # BUY | SELL
    qty: int
    entry_price: Decimal = Field(decimal_places=4, max_digits=18)
    target_price: Optional[Decimal] = Field(default=None, decimal_places=4, max_digits=18)
    stop_loss: Optional[Decimal] = Field(default=None, decimal_places=4, max_digits=18)
    exit_price: Optional[Decimal] = Field(default=None, decimal_places=4, max_digits=18)
    signal_id: Optional[uuid.UUID] = Field(default=None, nullable=True)
    status: str = Field(default="open", max_length=16)  # open|closed|sl_hit|target_hit|cancelled
    pnl: Optional[Decimal] = Field(default=None, decimal_places=4, max_digits=18)
    pnl_pct: Optional[Decimal] = Field(default=None, decimal_places=4, max_digits=14)
    entry_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )
    exit_at: Optional[datetime] = Field(default=None)
    notes: Optional[str] = Field(default=None)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )
