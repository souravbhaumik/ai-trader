"""Screener service — live stock screener backed by the stock_universe table.

Fetches live quotes for the requested page of stocks via the user's configured
broker adapter.  When no broker is configured, all price fields are 0.00 —
EOD data is never displayed as current price.

Live prices: Angel One (per-user) → Redis cache (30s TTL) → API response.
No broker configured = 0.00 everywhere (single source of truth rule).
EOD prices (ohlcv_daily) are used ONLY by the ML signal engine, never here.
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

    # ── 2. Fetch live quotes via broker adapter ───────────────────────────────
    # Single source of truth for prices: broker adapter → Redis cache → response.
    # NoBroker / unconfigured adapter returns [] for get_quotes_batch() so all
    # prices stay 0.00.  ohlcv_daily is NEVER used here for display prices.
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
            from app.services import price_service  # noqa: PLC0415
            quotes = await price_service.get_quotes_batch(adapter, symbols)
            quote_map = {q.symbol: q.__dict__ for q in quotes}
        except Exception as e:
            logger.error("screener_quotes_failed", err=str(e))

        if quote_map and redis:
            try:
                await redis.setex(cache_key, _SCREENER_CACHE_TTL, json.dumps(quote_map))
            except Exception:
                pass

    # ── 3. Assemble result rows ───────────────────────────────────────────────
    configured = adapter.is_credentials_configured()
    result_rows = []
    for sym, name, sector, market_cap, in_nifty50, in_nifty500 in rows:
        q_data = quote_map.get(sym, {})
        result_rows.append({
            "symbol":       sym,
            "name":         name,
            "sector":       sector or "Unknown",
            "market_cap":   market_cap,
            "in_nifty50":   in_nifty50,
            "in_nifty500":  in_nifty500,
            "price":        q_data.get("price", 0.0),
            "change_pct":   q_data.get("change_pct", 0.0),
            "volume":       q_data.get("volume", 0),
            "high":         q_data.get("high", 0.0),
            "low":          q_data.get("low", 0.0),
            "data_source":  adapter.broker_name if configured else "none",
            "data_live":    configured,
        })

    # ── 4. Optional signal filter (join against ML signals table) ───────────
    if signal_filter and signal_filter.upper() in ("BUY", "SELL", "HOLD"):
        sig = signal_filter.upper()
        # Fetch latest signal per symbol from the signals table
        try:
            signal_rows = await db.execute(
                text("""
                    SELECT DISTINCT ON (symbol) symbol, signal_type
                    FROM   signals
                    WHERE  symbol = ANY(:syms)
                    ORDER  BY symbol, ts DESC
                """),
                {"syms": [r["symbol"] for r in result_rows]},
            )
            signal_map = {row[0]: row[1] for row in signal_rows.fetchall()}
            result_rows = [
                r for r in result_rows
                if signal_map.get(r["symbol"], "").upper() == sig
            ]
        except Exception as exc:
            logger.warning("screener.signal_filter_failed", err=str(exc))
            # Fall through without filtering if signals table doesn't exist yet

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
