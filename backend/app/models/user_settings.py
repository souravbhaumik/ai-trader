"""UserSettings SQLModel — maps to the `user_settings` table."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlmodel import Field, SQLModel


class UserSettings(SQLModel, table=True):
    __tablename__ = "user_settings"

    user_id: uuid.UUID = Field(foreign_key="users.id", primary_key=True)

    trading_mode: str = Field(default="paper", max_length=10)
    paper_balance: Decimal = Field(default=Decimal("1000000.00"))
    max_position_pct: Decimal = Field(default=Decimal("10.00"))
    daily_loss_limit_pct: Decimal = Field(default=Decimal("5.00"))
    notification_signals: bool = Field(default=True)
    notification_orders: bool = Field(default=True)
    notification_news: bool = Field(default=True)
    preferred_broker: Optional[str] = Field(default=None, max_length=20)

    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )
    tbl_last_dt: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )
