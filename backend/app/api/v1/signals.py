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
    sig_type: Optional[str]  = Query(None, alias="type", description="BUY / SELL / HOLD — or comma-separated e.g. BUY,SELL"),
    active:   Optional[bool]  = Query(None),
):
    """Return paginated signal history. Signals are generated in Phase 3."""
    where_clauses = ["1=1"]
    params: dict = {}

    if symbol:
        where_clauses.append("symbol = :symbol")
        params["symbol"] = symbol.upper()
    if sig_type:
        valid = {"BUY", "SELL", "HOLD"}
        types = [t.strip().upper() for t in sig_type.split(",") if t.strip().upper() in valid]
        if len(types) == 1:
            where_clauses.append("signal_type = :sig_type")
            params["sig_type"] = types[0]
        elif len(types) > 1:
            placeholders = ", ".join(f":sig_type_{i}" for i in range(len(types)))
            where_clauses.append(f"signal_type IN ({placeholders})")
            for i, t in enumerate(types):
                params[f"sig_type_{i}"] = t
    if active is True:
        where_clauses.append("is_active = TRUE")
    elif active is False:
        pass  # no filter — return all

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
                   entry_price, target_price, stop_loss, model_version, is_active,
                   explanation
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
            "explanation":   row[10],
        })

    return {
        "total":    total,
        "page":     page,
        "per_page": per_page,
        "signals":  signals,
        "note":     "Signal generation starts in Phase 3 (ML Pipeline)." if total == 0 else None,
    }


# ---------------------------------------------------------------------------
# Phase 9: Signal Analytics & Win Rate Metrics
# ---------------------------------------------------------------------------

@router.get("/analytics/performance")
async def get_signal_performance(
    user: Annotated[User, Depends(get_current_user)],
    session: AsyncSession = Depends(get_session),
    period_days: int = Query(30, ge=7, le=365, description="Lookback period in days"),
):
    """Get aggregated signal performance metrics (win rates, returns, etc.)."""
    from app.services.signal_analytics_service import get_signal_performance_metrics
    
    metrics = await get_signal_performance_metrics(session, period_days=period_days)
    
    return {
        "period_days": metrics.period_days,
        "total_signals": metrics.total_signals,
        "evaluated_signals": metrics.evaluated_signals,
        "target_hit_rate": metrics.target_hit_rate,
        "stoploss_hit_rate": metrics.stoploss_hit_rate,
        "win_rate": metrics.target_hit_rate,  # alias for clarity
        "hit_target_count": metrics.hit_target_count,
        "hit_stoploss_count": metrics.hit_stoploss_count,
        "still_open_count": metrics.still_open_count,
        "returns": {
            "avg_1d": metrics.avg_return_1d,
            "avg_3d": metrics.avg_return_3d,
            "avg_5d": metrics.avg_return_5d,
        },
        "risk_metrics": {
            "avg_max_gain": metrics.avg_max_gain,
            "avg_max_drawdown": metrics.avg_max_drawdown,
        },
        "by_type": {
            "buy": {"count": metrics.buy_count, "win_rate": metrics.buy_win_rate},
            "sell": {"count": metrics.sell_count, "win_rate": metrics.sell_win_rate},
        },
    }


@router.get("/analytics/outcomes")
async def get_signal_outcomes(
    user: Annotated[User, Depends(get_current_user)],
    session: AsyncSession = Depends(get_session),
    limit: int = Query(20, ge=1, le=100),
    symbol: Optional[str] = Query(None),
):
    """Get recent signal outcomes with win/loss labels."""
    from app.services.signal_analytics_service import get_recent_signal_outcomes
    
    outcomes = await get_recent_signal_outcomes(session, limit=limit, symbol=symbol)
    
    return {
        "outcomes": [
            {
                "signal_id": o.signal_id,
                "symbol": o.symbol,
                "signal_type": o.signal_type,
                "signal_ts": o.signal_ts.isoformat(),
                "entry_price": o.entry_price,
                "target_price": o.target_price,
                "stop_loss": o.stop_loss,
                "confidence": o.confidence,
                "return_1d_pct": o.return_1d_pct,
                "return_5d_pct": o.return_5d_pct,
                "hit_target": o.hit_target,
                "hit_stoploss": o.hit_stoploss,
                "is_evaluated": o.is_evaluated,
                "outcome": o.outcome_label,
            }
            for o in outcomes
        ],
    }


@router.get("/analytics/by-sector")
async def get_performance_by_sector(
    user: Annotated[User, Depends(get_current_user)],
    session: AsyncSession = Depends(get_session),
    period_days: int = Query(30, ge=7, le=365),
):
    """Get signal performance broken down by sector."""
    from app.services.signal_analytics_service import get_performance_by_sector
    
    sectors = await get_performance_by_sector(session, period_days=period_days)
    return {"sectors": sectors}


@router.get("/analytics/trend")
async def get_daily_performance_trend(
    user: Annotated[User, Depends(get_current_user)],
    session: AsyncSession = Depends(get_session),
    period_days: int = Query(30, ge=7, le=90),
):
    """Get daily signal performance trend for charting."""
    from app.services.signal_analytics_service import get_daily_performance_trend
    
    trend = await get_daily_performance_trend(session, period_days=period_days)
    return {"trend": trend}
