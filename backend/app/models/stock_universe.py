"""StockUniverse SQLModel � maps to the `stock_universe` table."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlmodel import Field, SQLModel


class StockUniverse(SQLModel, table=True):
    __tablename__ = "stock_universe"

    symbol: str = Field(max_length=32, primary_key=True)  # e.g. RELIANCE.NS
    name: str = Field(default="", max_length=200)
    exchange: str = Field(default="NSE", max_length=10)
    sector: str = Field(default="Unknown", max_length=100)
    industry: str = Field(default="", max_length=100)
    market_cap: Optional[int] = Field(default=None)  # rupees
    is_etf: bool = Field(default=False)
    is_active: bool = Field(default=True)
    in_nifty50: bool = Field(default=False)
    in_nifty500: bool = Field(default=False)
    logo_path: Optional[str] = Field(default=None, max_length=300)  # local PNG path

    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )
    tbl_last_dt: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None)
    )
