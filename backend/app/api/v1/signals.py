"""Signals API — AI-generated trading signals (Phase 3 fills the data).

Phase 2: returns empty list with metadata so the frontend can display
the correct empty state and broker info.
"""
from __future__ import annotations

from typing import Annotated, Optional

import structlog
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.api.v1.deps import get_current_user, get_current_user_settings
from app.core.database import get_session
from app.models.user import User
from app.models.user_settings import UserSettings

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/signals", tags=["signals"])


@router.get("")
async def list_signals(
    user: Annotated[User, Depends(get_current_user)],
    user_settings: Annotated[UserSettings, Depends(get_current_user_settings)],
    session: AsyncSession = Depends(get_session),
    page:     int            = Query(1,    ge=1),
    per_page: int            = Query(50,   ge=1, le=200),
    symbol:   Optional[str]  = Query(None),
    sig_type: Optional[str]  = Query(None, alias="type", description="BUY / SELL / HOLD"),
    active:   bool           = Query(True),
):
    """Return paginated signal history. Signals are generated in Phase 3."""
    where_clauses = ["1=1"]
    params: dict = {}

    if symbol:
        where_clauses.append("symbol = :symbol")
        params["symbol"] = symbol.upper()
    if sig_type and sig_type.upper() in ("BUY", "SELL", "HOLD"):
        where_clauses.append("signal_type = :sig_type")
        params["sig_type"] = sig_type.upper()
    if active:
        where_clauses.append("is_active = TRUE")

    where_sql = " AND ".join(where_clauses)

    count_result = await session.execute(
        text(f"SELECT COUNT(*) FROM signals WHERE {where_sql}"), params
    )
    total = count_result.scalar_one()

    params["limit"]  = per_page
    params["offset"] = (page - 1) * per_page

    data_result = await session.execute(
        text(f"""
            SELECT id, symbol, ts, signal_type, confidence,
                   entry_price, target_price, stop_loss, model_version, is_active
            FROM signals
            WHERE {where_sql}
            ORDER BY ts DESC
            LIMIT :limit OFFSET :offset
        """),
        params,
    )
    rows = data_result.fetchall()

    signals = []
    for row in rows:
        signals.append({
            "id":            str(row[0]),
            "symbol":        row[1],
            "ts":            row[2].isoformat() if row[2] else None,
            "signal_type":   row[3],
            "confidence":    float(row[4]) if row[4] else 0.0,
            "entry_price":   float(row[5]) if row[5] else None,
            "target_price":  float(row[6]) if row[6] else None,
            "stop_loss":     float(row[7]) if row[7] else None,
            "model_version": row[8],
            "is_active":     row[9],
        })

    return {
        "total":    total,
        "page":     page,
        "per_page": per_page,
        "signals":  signals,
        "note":     "Signal generation starts in Phase 3 (ML Pipeline)." if total == 0 else None,
    }
