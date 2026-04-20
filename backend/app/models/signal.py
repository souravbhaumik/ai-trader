"""Signal SQLModel � maps to the `signals` hypertable."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Dict, Optional

from sqlalchemy import Column
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class Signal(SQLModel, table=True):
    __tablename__ = "signals"

    id: uuid.UUID = Field(
        default_factory=uuid.uuid4,
        primary_key=True,
        nullable=False,
    )
    symbol: str = Field(max_length=32, index=True)
    ts: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )
    signal_type: str = Field(max_length=10)  # BUY / SELL / HOLD
    confidence: Decimal = Field(default=Decimal("0"))  # 0.0 � 1.0
    entry_price: Optional[Decimal] = Field(default=None)
    target_price: Optional[Decimal] = Field(default=None)
    stop_loss: Optional[Decimal] = Field(default=None)
    model_version: str = Field(default="v0", max_length=50)
    features: Optional[Dict[str, Any]] = Field(
        default=None,
        sa_column=Column(JSONB),
    )
    is_active: bool = Field(default=True)
    explanation: Optional[str] = Field(default=None, max_length=1000)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )
