"""Signals API — AI-generated trading signals (Phase 3 fills the data).

Phase 2: returns empty list with metadata so the frontend can display
the correct empty state and broker info.
"""
from __future__ import annotations

from typing import Annotated, Optional
import csv
import io
from datetime import datetime, timedelta

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
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
                "price_1d": o.price_1d,
                "price_3d": o.price_3d,
                "price_5d": o.price_5d,
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


@router.get("/analytics/export/csv")
async def export_signal_outcomes_csv(
    user: Annotated[User, Depends(get_current_user)],
    session: AsyncSession = Depends(get_session),
    period_days: int = Query(30, ge=1, le=365),
):
    """Export raw signal_outcomes rows as a CSV download."""
    cutoff = datetime.utcnow() - timedelta(days=period_days)

    result = await session.execute(
        text("""
            SELECT
                so.signal_id,
                so.symbol,
                so.signal_type,
                so.signal_ts,
                so.entry_price,
                so.target_price,
                so.stop_loss,
                so.confidence,
                so.price_1d,
                so.price_3d,
                so.price_5d,
                so.return_1d_pct,
                so.return_3d_pct,
                so.return_5d_pct,
                so.hit_target,
                so.hit_stoploss,
                so.hit_target_at,
                so.hit_stoploss_at,
                so.max_gain_pct,
                so.max_drawdown_pct,
                so.is_evaluated,
                so.evaluated_at,
                so.created_at,
                so.tbl_last_dt
            FROM signal_outcomes so
            WHERE so.signal_ts >= :cutoff
            ORDER BY so.signal_ts DESC
        """),
        {"cutoff": cutoff},
    )
    rows = result.fetchall()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "signal_id", "symbol", "signal_type", "signal_ts",
        "entry_price", "target_price", "stop_loss", "confidence",
        "price_1d", "price_3d", "price_5d",
        "return_1d_pct", "return_3d_pct", "return_5d_pct",
        "hit_target", "hit_stoploss", "hit_target_at", "hit_stoploss_at",
        "max_gain_pct", "max_drawdown_pct",
        "is_evaluated", "evaluated_at", "created_at", "tbl_last_dt",
    ])
    for row in rows:
        writer.writerow([
            str(row.signal_id),
            row.symbol,
            row.signal_type,
            row.signal_ts.isoformat() if row.signal_ts else "",
            row.entry_price,
            row.target_price,
            row.stop_loss,
            row.confidence,
            row.price_1d,
            row.price_3d,
            row.price_5d,
            row.return_1d_pct,
            row.return_3d_pct,
            row.return_5d_pct,
            row.hit_target,
            row.hit_stoploss,
            row.hit_target_at.isoformat() if row.hit_target_at else "",
            row.hit_stoploss_at.isoformat() if row.hit_stoploss_at else "",
            row.max_gain_pct,
            row.max_drawdown_pct,
            row.is_evaluated,
            row.evaluated_at.isoformat() if row.evaluated_at else "",
            row.created_at.isoformat() if row.created_at else "",
            row.tbl_last_dt.isoformat() if row.tbl_last_dt else "",
        ])

    output.seek(0)
    filename = f"signal_outcomes_{period_days}d_{datetime.utcnow().strftime('%Y%m%d')}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# On-demand single-symbol signal refresh
# ---------------------------------------------------------------------------

@router.post("/{symbol}/refresh")
async def refresh_signal(
    symbol: str,
    user: Annotated[User, Depends(get_current_user)],
    session: AsyncSession = Depends(get_session),
):
    """Trigger an on-demand signal refresh for a single symbol.

    This runs: news fetch → FinBERT scoring → signal recomputation for the
    requested symbol only.  Typical latency 2–5 seconds.

    Rate-limited to one refresh per symbol per user per 3 minutes.  Returns
    HTTP 429 if the lock is still held.

    Returns the freshly computed signal dict on success.
    """
    import json
    import redis as _redis
    from app.core.config import settings

    sym = symbol.upper().split(".")[0]
    user_id = str(user.id)

    # ── Rate limit: 1 refresh per symbol per user every 180 seconds ───────────
    r_sync = _redis.from_url(settings.redis_url, decode_responses=True)
    lock_key = f"refresh_lock:{user_id}:{sym}"
    if r_sync.exists(lock_key):
        ttl = r_sync.ttl(lock_key)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Rate limited. Please wait {ttl}s before refreshing {sym} again.",
        )
    r_sync.setex(lock_key, 180, "1")

    # ── 1. Fetch fresh news for this symbol ────────────────────────────────────
    try:
        from app.services.news_fetcher import fetch_google_news, fetch_yahoo_finance_news

        # Fetch DB company name for better GNews query
        row = await session.execute(
            text("SELECT name FROM stock_universe WHERE symbol = :sym LIMIT 1"),
            {"sym": sym},
        )
        company_row = row.fetchone()
        company_name = company_row[0] if company_row else sym

        gn_articles = fetch_google_news([company_name], max_per_symbol=10)
        yf_articles = fetch_yahoo_finance_news([sym + ".NS"], max_per_ticker=10)
        fresh_articles = gn_articles + yf_articles

        if fresh_articles:
            from app.services.ner_mapper import map_headline_to_symbols
            from app.services.sentiment_scorer import score_headlines
            import hashlib, uuid as _uuid
            from datetime import datetime, timezone

            mapped = []
            for art in fresh_articles:
                ticker_hint = art.get("_ticker")
                if ticker_hint:
                    art_sym = ticker_hint.removesuffix(".NS")
                    if art_sym == sym:
                        mapped.append({**art, "symbol": sym})
                else:
                    syms = map_headline_to_symbols(art["title"], query_hint=art.get("_query"))
                    if sym in syms:
                        mapped.append({**art, "symbol": sym})

            if mapped:
                texts = [
                    (a["title"] + ". " + a["summary"]).strip() if a.get("summary") else a["title"]
                    for a in mapped
                ]
                scores = score_headlines(texts)
                import math
                now_utc = datetime.now(tz=timezone.utc)
                total_w, w_sum = 0.0, 0.0
                for result in scores:
                    polarity = result.score * 2 - 1
                    weight   = result.confidence
                    w_sum   += polarity * weight
                    total_w += weight
                agg = round(w_sum / total_w, 4) if total_w else 0.0
                payload = json.dumps({
                    "symbol": sym, "score": agg,
                    "article_count": len(scores),
                    "last_updated": now_utc.isoformat(),
                })
                r_sync.setex(f"sentiment:{sym}", 7200, payload)
    except Exception as exc:
        logger.warning("signal_refresh.news_failed", symbol=sym, err=str(exc))
        # Continue with whatever was in cache

    # ── 2. Recompute signal (synchronous, runs in thread pool) ───────────────
    import asyncio
    from functools import partial

    def _compute_signal(sym: str) -> Optional[dict]:
        from app.tasks.signal_generator import generate_signals as _gen
        from app.core.database import get_sync_session as _gsync
        from app.services.feature_engineer import build_features, FEATURE_NAMES
        from app.services.ml_loader import predict as ml_predict
        import numpy as np, json as _json, uuid as _uuid, os

        _W_TECH      = float(os.getenv("SIGNAL_WEIGHT_TECH",      "0.40"))
        _W_ML        = float(os.getenv("SIGNAL_WEIGHT_ML",        "0.45"))
        _W_SENTIMENT = float(os.getenv("SIGNAL_WEIGHT_SENTIMENT", "0.15"))

        try:
            raw = r_sync.get(f"sentiment:{sym}")
            sentiment = float(_json.loads(raw).get("score", 0.0)) if raw else 0.0
        except Exception:
            sentiment = 0.0

        try:
            with _gsync() as db:
                rows = db.execute(
                    text("""
                        SELECT close, high, low, volume
                        FROM   ohlcv_daily
                        WHERE  symbol = :s
                        ORDER  BY ts DESC LIMIT 90
                    """),
                    {"s": sym},
                ).fetchall()
            if len(rows) < 28:
                return None
            rows_asc = list(reversed(rows))
            closes  = [float(r[0]) for r in rows_asc]
            highs   = [float(r[1]) for r in rows_asc]
            lows    = [float(r[2]) for r in rows_asc]
            volumes = [float(r[3]) for r in rows_asc]

            from app.tasks.signal_generator import _score_symbol
            tech = _score_symbol(closes)
            if tech is None:
                return None

            tech_dir  = tech["signal_type"]
            tech_conf = tech["confidence"]
            final_dir  = tech_dir
            final_conf = tech_conf
            features   = tech["features"]
            model_ver  = "technical-v1"

            ml_available = ml_predict(dict.fromkeys(FEATURE_NAMES, 0.0)) is not None
            if ml_available:
                s = max(-1.0, min(1.0, sentiment))
                feat_vec = build_features(sym, closes, highs, lows, volumes, s)
                ml_result = ml_predict(feat_vec)
                if ml_result and ml_result["direction"] != "HOLD":
                    ml_dir   = ml_result["direction"]
                    ml_prob  = ml_result["probability"]
                    ml_score = ml_prob if ml_dir == "BUY" else (1 - ml_prob)
                    sent_score = (s + 1) / 2
                    tech_score = tech_conf if tech_dir == "BUY" else (1 - tech_conf)
                    blended = _W_TECH * tech_score + _W_ML * ml_score + _W_SENTIMENT * sent_score
                    final_dir  = "BUY" if blended >= 0.5 else "SELL"
                    final_conf = abs(blended - 0.5) * 2
                    model_ver  = ml_result["version"]
                    features["ml_probability"]  = round(ml_prob, 4)
                    features["sentiment_score"] = round(s, 4)

            entry  = tech["entry_price"]
            target = tech["target_price"]
            sl     = tech["stop_loss"]

            from datetime import datetime as _dt
            import uuid as _uuid2, json as _json2

            sig_id = _uuid2.uuid4()
            now_ts = _dt.utcnow()
            with _gsync() as db:
                db.execute(
                    text("UPDATE signals SET is_active = FALSE WHERE symbol = :s AND is_active = TRUE"),
                    {"s": sym},
                )
                db.execute(
                    text("""
                        INSERT INTO signals
                            (id, symbol, ts, signal_type, confidence,
                             entry_price, target_price, stop_loss,
                             model_version, features, is_active, created_at)
                        VALUES
                            (:id, :sym, :ts, :st, :conf,
                             :entry, :target, :sl,
                             :mv, :feat, TRUE, :created_at)
                    """),
                    {
                        "id":         str(sig_id),
                        "sym":        sym,
                        "ts":         now_ts,
                        "st":         final_dir,
                        "conf":       round(final_conf, 4),
                        "entry":      entry,
                        "target":     target,
                        "sl":         sl,
                        "mv":         model_ver,
                        "feat":       _json2.dumps(features),
                        "created_at": now_ts,
                    },
                )
                db.commit()
            return {
                "id":            str(sig_id),
                "symbol":        sym,
                "ts":            now_ts.isoformat(),
                "signal_type":   final_dir,
                "confidence":    round(final_conf, 4),
                "entry_price":   float(entry) if entry is not None else None,
                "target_price":  float(target) if target is not None else None,
                "stop_loss":     float(sl) if sl is not None else None,
                "model_version": model_ver,
                "is_active":     True,
                "sentiment_score": round(sentiment, 4),
                "refreshed":     True,
            }
        except Exception as exc:
            logger.error("signal_refresh.compute_failed", symbol=sym, err=str(exc))
            return None

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(None, partial(_compute_signal, sym))

    if result is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Could not compute signal for {sym} — insufficient data or no technical trigger.",
        )

    logger.info("signal_refresh.done", symbol=sym, signal_type=result["signal_type"],
                confidence=result["confidence"])
    return result
