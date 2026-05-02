"""add_delivery_pct_to_ohlcv

Revision ID: 0011_add_delivery_pct
Revises: 0010_forecast_history
Create Date: 2026-05-02

Adds delivery_pct (FLOAT, nullable) to ohlcv_daily and ohlcv_intraday.

delivery_pct stores the NSE-published Delivery Percentage [0.0, 1.0]:
  - 0.0 = 0% delivery (100% intraday / day-trading volume)
  - 1.0 = 100% delivery (all shares traded were taken to delivery)
  - NULL = column not yet populated (historical rows before migration)

This column is the foundation of Phase 11 Institutional Alpha features:
  delivery_pct, delivery_slope (5-day), and smart_money_idx.
Source: NSE sec_bhavdata_full_{date}.csv column DELIV_PER.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0011_add_delivery_pct'
down_revision: Union[str, None] = '0010_forecast_history'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # NULL allowed — historical rows won't have delivery data
    op.add_column('ohlcv_daily', sa.Column('delivery_pct', sa.Float(), nullable=True))
    op.add_column('ohlcv_intraday', sa.Column('delivery_pct', sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column('ohlcv_intraday', 'delivery_pct')
    op.drop_column('ohlcv_daily', 'delivery_pct')
