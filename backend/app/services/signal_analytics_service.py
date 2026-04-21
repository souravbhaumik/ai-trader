"""Signal analytics service — Phase 9.

Provides win rate metrics and signal performance analytics for:
- Dashboard display
- User transparency
- Model performance monitoring
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


@dataclass
class SignalPerformanceMetrics:
    """Aggregated signal performance metrics."""
    period_days: int
    total_signals: int
    evaluated_signals: int
    
    # Target/SL stats
    hit_target_count: int
    hit_stoploss_count: int
    still_open_count: int
    
    # Win rates
    target_hit_rate: float  # percentage
    stoploss_hit_rate: float
    
    # Returns
    avg_return_1d: Optional[float]
    avg_return_3d: Optional[float]
    avg_return_5d: Optional[float]
    
    # Risk metrics
    avg_max_gain: Optional[float]
    avg_max_drawdown: Optional[float]
    
    # By signal type
    buy_count: int
    sell_count: int
    buy_win_rate: float
    sell_win_rate: float


@dataclass
class SignalOutcomeSummary:
    """Individual signal outcome for display."""
    signal_id: str
    symbol: str
    signal_type: str
    signal_ts: datetime
    entry_price: float
    target_price: Optional[float]
    stop_loss: Optional[float]
    confidence: float
    price_1d: Optional[float]   # actual close on day+1
    price_3d: Optional[float]   # actual close on day+3
    price_5d: Optional[float]   # actual close on day+5
    return_1d_pct: Optional[float]
    return_5d_pct: Optional[float]
    hit_target: bool
    hit_stoploss: bool
    is_evaluated: bool
    outcome_label: str  # "WIN" | "LOSS" | "PENDING" | "NEUTRAL"


async def get_signal_performance_metrics(
    db: AsyncSession,
    period_days: int = 30,
) -> SignalPerformanceMetrics:
    """Get aggregated signal performance metrics for the specified period."""
    cutoff_date = datetime.now() - timedelta(days=period_days)
    
    # Main aggregation query
    result = await db.execute(
        text("""
            SELECT 
                COUNT(*) as total_signals,
                COUNT(CASE WHEN is_evaluated THEN 1 END) as evaluated_signals,
                COUNT(CASE WHEN hit_target THEN 1 END) as hit_target_count,
                COUNT(CASE WHEN hit_stoploss THEN 1 END) as hit_stoploss_count,
                COUNT(CASE WHEN NOT is_evaluated AND NOT hit_target AND NOT hit_stoploss THEN 1 END) as still_open_count,
                AVG(return_1d_pct) as avg_return_1d,
                AVG(return_3d_pct) as avg_return_3d,
                AVG(return_5d_pct) as avg_return_5d,
                AVG(max_gain_pct) as avg_max_gain,
                AVG(max_drawdown_pct) as avg_max_drawdown,
                COUNT(CASE WHEN signal_type = 'BUY' THEN 1 END) as buy_count,
                COUNT(CASE WHEN signal_type = 'SELL' THEN 1 END) as sell_count,
                COUNT(CASE WHEN signal_type = 'BUY' AND hit_target THEN 1 END) as buy_wins,
                COUNT(CASE WHEN signal_type = 'SELL' AND hit_target THEN 1 END) as sell_wins,
                -- Denominator fix: count only evaluated signals per direction for win-rate
                COUNT(CASE WHEN signal_type = 'BUY' AND is_evaluated THEN 1 END) as buy_evaluated,
                COUNT(CASE WHEN signal_type = 'SELL' AND is_evaluated THEN 1 END) as sell_evaluated
            FROM signal_outcomes
            WHERE signal_ts >= :cutoff
        """),
        {"cutoff": cutoff_date},
    )
    row = result.first()
    
    if not row or row.total_signals == 0:
        return SignalPerformanceMetrics(
            period_days=period_days,
            total_signals=0,
            evaluated_signals=0,
            hit_target_count=0,
            hit_stoploss_count=0,
            still_open_count=0,
            target_hit_rate=0.0,
            stoploss_hit_rate=0.0,
            avg_return_1d=None,
            avg_return_3d=None,
            avg_return_5d=None,
            avg_max_gain=None,
            avg_max_drawdown=None,
            buy_count=0,
            sell_count=0,
            buy_win_rate=0.0,
            sell_win_rate=0.0,
        )
    
    total = row.total_signals
    evaluated = row.evaluated_signals or 0
    
    # Calculate win rates (only from evaluated signals)
    target_hit_rate = (row.hit_target_count / evaluated * 100) if evaluated > 0 else 0.0
    stoploss_hit_rate = (row.hit_stoploss_count / evaluated * 100) if evaluated > 0 else 0.0
    
    buy_evaluated  = row.buy_evaluated  or 0
    sell_evaluated = row.sell_evaluated or 0

    buy_win_rate  = (row.buy_wins  / buy_evaluated  * 100) if buy_evaluated  > 0 else 0.0
    sell_win_rate = (row.sell_wins / sell_evaluated * 100) if sell_evaluated > 0 else 0.0
    
    return SignalPerformanceMetrics(
        period_days=period_days,
        total_signals=total,
        evaluated_signals=evaluated,
        hit_target_count=row.hit_target_count or 0,
        hit_stoploss_count=row.hit_stoploss_count or 0,
        still_open_count=row.still_open_count or 0,
        target_hit_rate=round(target_hit_rate, 1),
        stoploss_hit_rate=round(stoploss_hit_rate, 1),
        avg_return_1d=round(float(row.avg_return_1d), 2) if row.avg_return_1d else None,
        avg_return_3d=round(float(row.avg_return_3d), 2) if row.avg_return_3d else None,
        avg_return_5d=round(float(row.avg_return_5d), 2) if row.avg_return_5d else None,
        avg_max_gain=round(float(row.avg_max_gain), 2) if row.avg_max_gain else None,
        avg_max_drawdown=round(float(row.avg_max_drawdown), 2) if row.avg_max_drawdown else None,
        buy_count=row.buy_count or 0,
        sell_count=row.sell_count or 0,
        buy_win_rate=round(buy_win_rate, 1),
        sell_win_rate=round(sell_win_rate, 1),
    )


async def get_recent_signal_outcomes(
    db: AsyncSession,
    limit: int = 20,
    symbol: Optional[str] = None,
) -> List[SignalOutcomeSummary]:
    """Get recent signal outcomes for display."""
    query = """
        SELECT 
            signal_id, symbol, signal_type, signal_ts,
            entry_price, target_price, stop_loss, confidence,
            price_1d, price_3d, price_5d,
            return_1d_pct, return_5d_pct,
            hit_target, hit_stoploss, is_evaluated
        FROM signal_outcomes
        WHERE 1=1
    """
    params: Dict = {"limit": limit}
    
    if symbol:
        query += " AND symbol = :symbol"
        params["symbol"] = symbol
    
    query += " ORDER BY signal_ts DESC LIMIT :limit"
    
    result = await db.execute(text(query), params)
    rows = result.fetchall()
    
    outcomes = []
    for row in rows:
        # Determine outcome label
        if row.hit_target:
            outcome_label = "WIN"
        elif row.hit_stoploss:
            outcome_label = "LOSS"
        elif not row.is_evaluated:
            outcome_label = "PENDING"
        else:
            # Evaluated but neither target nor SL hit
            if row.return_5d_pct and row.return_5d_pct > 0:
                outcome_label = "WIN"
            elif row.return_5d_pct and row.return_5d_pct < 0:
                outcome_label = "LOSS"
            else:
                outcome_label = "NEUTRAL"
        
        outcomes.append(SignalOutcomeSummary(
            signal_id=str(row.signal_id),
            symbol=row.symbol,
            signal_type=row.signal_type,
            signal_ts=row.signal_ts,
            entry_price=float(row.entry_price),
            target_price=float(row.target_price) if row.target_price else None,
            stop_loss=float(row.stop_loss) if row.stop_loss else None,
            confidence=float(row.confidence),
            price_1d=float(row.price_1d) if row.price_1d else None,
            price_3d=float(row.price_3d) if row.price_3d else None,
            price_5d=float(row.price_5d) if row.price_5d else None,
            return_1d_pct=float(row.return_1d_pct) if row.return_1d_pct else None,
            return_5d_pct=float(row.return_5d_pct) if row.return_5d_pct else None,
            hit_target=row.hit_target,
            hit_stoploss=row.hit_stoploss,
            is_evaluated=row.is_evaluated,
            outcome_label=outcome_label,
        ))
    
    return outcomes


async def get_performance_by_sector(
    db: AsyncSession,
    period_days: int = 30,
) -> List[Dict]:
    """Get signal performance broken down by sector."""
    cutoff_date = datetime.now() - timedelta(days=period_days)
    
    result = await db.execute(
        text("""
            SELECT 
                su.sector,
                COUNT(*) as total_signals,
                COUNT(CASE WHEN so.hit_target THEN 1 END) as wins,
                AVG(so.return_5d_pct) as avg_return
            FROM signal_outcomes so
            JOIN stock_universe su ON su.symbol = so.symbol
            WHERE so.signal_ts >= :cutoff AND so.is_evaluated
            GROUP BY su.sector
            ORDER BY COUNT(*) DESC
            LIMIT 10
        """),
        {"cutoff": cutoff_date},
    )
    
    sectors = []
    for row in result.fetchall():
        win_rate = (row.wins / row.total_signals * 100) if row.total_signals > 0 else 0
        sectors.append({
            "sector": row.sector,
            "total_signals": row.total_signals,
            "win_rate": round(win_rate, 1),
            "avg_return": round(float(row.avg_return), 2) if row.avg_return else 0,
        })
    
    return sectors


async def get_daily_performance_trend(
    db: AsyncSession,
    period_days: int = 30,
) -> List[Dict]:
    """Get daily signal performance trend for charting."""
    cutoff_date = datetime.now() - timedelta(days=period_days)
    
    result = await db.execute(
        text("""
            SELECT 
                DATE(signal_ts) as signal_date,
                COUNT(*) as total_signals,
                COUNT(CASE WHEN hit_target THEN 1 END) as wins,
                AVG(return_1d_pct) as avg_return
            FROM signal_outcomes
            WHERE signal_ts >= :cutoff AND is_evaluated
            GROUP BY DATE(signal_ts)
            ORDER BY signal_date ASC
        """),
        {"cutoff": cutoff_date},
    )
    
    trend = []
    for row in result.fetchall():
        win_rate = (row.wins / row.total_signals * 100) if row.total_signals > 0 else 0
        trend.append({
            "date": row.signal_date.isoformat(),
            "total_signals": row.total_signals,
            "win_rate": round(win_rate, 1),
            "avg_return": round(float(row.avg_return), 2) if row.avg_return else 0,
        })
    
    return trend
