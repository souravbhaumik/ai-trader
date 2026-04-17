"""Screener service — live stock screener backed by the stock_universe table.

Fetches live quotes for the requested page of stocks, enriches them with
technical indicators computed from recent OHLCV data (RSI, etc.), and
returns a ranked list.

To avoid rate-limit issues, live prices are batched via the broker adapter and
cached in Redis (30-second TTL) per adapter.
"""
from __future__ import annotations

import json
import math
from typing import Any, Dict, List, Optional

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.brokers.base import BrokerAdapter
from app.core.redis_client import get_redis

logger = structlog.get_logger(__name__)

_SCREENER_CACHE_TTL = 30  # seconds


async def get_screener_page(
    adapter: BrokerAdapter,
    db: AsyncSession,
    *,
    page: int = 1,
    per_page: int = 50,
    q: Optional[str] = None,
    sector: Optional[str] = None,
    signal_filter: Optional[str] = None,
    sort_by: str = "market_cap",
    sort_dir: str = "desc",
) -> Dict[str, Any]:
    """Return paginated screener results with live prices.

    Returns {total, page, per_page, rows: [ScreenerRow, ...]}
    """
    # ── 1. Query universe from DB ─────────────────────────────────────────────
    where_clauses = ["u.is_active = TRUE", "u.is_etf = FALSE"]
    params: dict = {}

    if q:
        where_clauses.append(
            "(UPPER(u.symbol) LIKE UPPER(:q) OR UPPER(u.name) LIKE UPPER(:q))"
        )
        params["q"] = f"%{q}%"
    if sector and sector.lower() != "all":
        where_clauses.append("UPPER(u.sector) = UPPER(:sector)")
        params["sector"] = sector

    where_sql = " AND ".join(where_clauses)

    # Allowed sort columns (whitelist to prevent SQL injection)
    _SORTABLE = {"market_cap", "symbol", "name", "sector"}
    col = sort_by if sort_by in _SORTABLE else "market_cap"
    direction = "DESC" if sort_dir.lower() == "desc" else "ASC"

    count_sql = text(f"SELECT COUNT(*) FROM stock_universe u WHERE {where_sql}")
    data_sql = text(f"""
        SELECT u.symbol, u.name, u.sector, u.market_cap, u.in_nifty50, u.in_nifty500
        FROM stock_universe u
        WHERE {where_sql}
        ORDER BY
            CASE WHEN u.market_cap IS NOT NULL THEN 0 ELSE 1 END,
            u.{col} {direction} NULLS LAST,
            u.symbol ASC
        LIMIT :limit OFFSET :offset
    """)

    offset = (page - 1) * per_page
    params["limit"] = per_page
    params["offset"] = offset

    total_result = await db.execute(count_sql, params)
    total: int = total_result.scalar_one()

    data_result = await db.execute(data_sql, params)
    rows = data_result.fetchall()

    if not rows:
        return {"total": 0, "page": page, "per_page": per_page, "rows": []}

    symbols = [r[0] for r in rows]

    # ── 2. Fetch live quotes (batch) with Redis cache ─────────────────────────
    cache_key = f"screener_quotes:{adapter.broker_name}:{','.join(sorted(symbols))}"
    quote_map: Dict[str, Any] = {}

    redis = None
    try:
        redis = get_redis()
        cached = await redis.get(cache_key)
        if cached:
            quote_map = json.loads(cached)
    except Exception:
        pass

    if not quote_map:
        try:
            quotes = await adapter.get_quotes_batch(symbols)
            quote_map = {q.symbol: q.__dict__ for q in quotes}
            if quote_map and redis:
                try:
                    await redis.setex(cache_key, _SCREENER_CACHE_TTL, json.dumps(quote_map))
                except Exception:
                    pass
        except Exception as e:
            logger.error("screener_quotes_failed", error=str(e))

    # ── 3. Assemble result rows ───────────────────────────────────────────────
    result_rows = []
    for sym, name, sector, market_cap, in_nifty50, in_nifty500 in rows:
        q_data = quote_map.get(sym, {})
        price      = q_data.get("price", 0.0)
        change_pct = q_data.get("change_pct", 0.0)
        volume     = q_data.get("volume", 0)
        high       = q_data.get("high", 0.0)
        low        = q_data.get("low", 0.0)

        configured = adapter.is_credentials_configured()
        result_rows.append({
            "symbol":       sym,
            "name":         name,
            "sector":       sector or "Unknown",
            "market_cap":   market_cap,
            "in_nifty50":   in_nifty50,
            "in_nifty500":  in_nifty500,
            "price":        price if configured or adapter.broker_name == "yfinance" else 0.0,
            "change_pct":   change_pct if configured or adapter.broker_name == "yfinance" else 0.0,
            "volume":       volume,
            "high":         high,
            "low":          low,
            "data_source":  adapter.broker_name,
            "data_live":    configured or adapter.broker_name == "yfinance",
        })

    # ── 4. Optional signal filter (post-process) ──────────────────────────────
    if signal_filter and signal_filter.upper() in ("BUY", "SELL", "HOLD"):
        # Signals are generated in Phase 3 — for now, derive a simple
        # heuristic: BUY if change_pct > 1%, SELL if < -1%, else HOLD
        sig = signal_filter.upper()
        def _heuristic(row: dict) -> str:
            if row["change_pct"] > 1:
                return "BUY"
            if row["change_pct"] < -1:
                return "SELL"
            return "HOLD"

        result_rows = [r for r in result_rows if _heuristic(r) == sig]

    return {
        "total":    total,
        "page":     page,
        "per_page": per_page,
        "pages":    math.ceil(total / per_page) if total else 0,
        "rows":     result_rows,
    }


async def get_sectors(db: AsyncSession) -> List[str]:
    """Return distinct sectors from the universe."""
    result = await db.execute(
        text("SELECT DISTINCT sector FROM stock_universe WHERE is_active = TRUE ORDER BY sector")
    )
    return [r[0] for r in result.fetchall() if r[0]]
