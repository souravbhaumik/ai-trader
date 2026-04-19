"""Admin browser endpoints — DB table explorer, SQL runner, Redis key browser."""
from __future__ import annotations

import json
from typing import Any

import redis.asyncio as aioredis
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from jose import JWTError
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.database import get_session
from app.core.security import decode_access_token
from app.models.user import User

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/admin/browser", tags=["admin-browser"])

# ── Auth helper (same pattern as users.py) ───────────────────────────────────

async def _require_admin(request: Request, session: AsyncSession) -> User:
    from sqlalchemy import select
    import uuid
    auth_hdr = request.headers.get("Authorization", "")
    if not auth_hdr.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing bearer token.")
    token = auth_hdr.removeprefix("Bearer ").strip()
    try:
        payload = decode_access_token(token)
    except JWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token.")
    if payload.get("role") != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin role required.")
    result = await session.execute(select(User).where(User.id == uuid.UUID(payload["sub"])))
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Account inactive.")
    return user


# ── DB: list tables ───────────────────────────────────────────────────────────

@router.get("/db/tables")
async def list_tables(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    await _require_admin(request, session)
    result = await session.execute(text("""
        SELECT
            t.table_name,
            pg_size_pretty(pg_total_relation_size(quote_ident(t.table_name))) AS size,
            GREATEST(
                COALESCE(s.n_live_tup, 0),
                COALESCE(c.reltuples::bigint, 0)
            ) AS row_estimate
        FROM information_schema.tables t
        LEFT JOIN pg_stat_user_tables s ON s.relname = t.table_name
        LEFT JOIN pg_class c ON c.relname = t.table_name AND c.relkind = 'r'
        WHERE t.table_schema = 'public'
          AND t.table_type = 'BASE TABLE'
        ORDER BY t.table_name
    """))
    rows = result.mappings().all()
    return [dict(r) for r in rows]


# ── DB: table rows ────────────────────────────────────────────────────────────

@router.get("/db/tables/{table_name}/rows")
async def table_rows(
    table_name: str,
    request: Request,
    limit: int = 20,
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _require_admin(request, session)
    # Validate table exists to prevent injection
    check = await session.execute(text(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema='public' AND table_name=:t"
    ), {"t": table_name})
    if check.first() is None:
        raise HTTPException(404, f"Table '{table_name}' not found.")
    limit = max(1, min(limit, 500))
    result = await session.execute(
        text(f'SELECT * FROM "{table_name}" LIMIT :lim'), {"lim": limit}
    )
    columns = list(result.keys())
    rows = [_serialize_row(dict(zip(columns, r))) for r in result.fetchall()]
    return {"columns": columns, "rows": rows}


# ── DB: custom SQL query ──────────────────────────────────────────────────────

class QueryRequest(BaseModel):
    sql: str


@router.post("/db/query")
async def run_query(
    body: QueryRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _require_admin(request, session)
    sql = body.sql.strip().rstrip(";").strip()
    # Block PL/pgSQL anonymous blocks only (DO $$ ... $$)
    normalized = sql.upper().lstrip()
    if normalized.startswith("DO"):
        raise HTTPException(400, "PL/pgSQL anonymous blocks (DO) are not allowed.")
    try:
        result = await session.execute(text(sql))
        await session.commit()
        try:
            columns = list(result.keys())
            rows = [_serialize_row(dict(zip(columns, r))) for r in result.fetchall()]
        except Exception:
            # Non-SELECT statements (UPDATE/INSERT/DELETE) return no rows
            columns = []
            rows = []
        return {"columns": columns, "rows": rows, "count": result.rowcount if result.rowcount >= 0 else len(rows)}
    except Exception as exc:
        await session.rollback()
        raise HTTPException(400, str(exc)) from exc


# ── Redis: list keys ──────────────────────────────────────────────────────────

@router.get("/redis/keys")
async def redis_keys(
    request: Request,
    pattern: str = "*",
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    await _require_admin(request, session)
    r = aioredis.from_url(
        settings.redis_url, encoding="utf-8", decode_responses=True
    )
    try:
        keys = await r.keys(pattern)
        keys = sorted(keys)[:200]  # cap at 200
        pipe = r.pipeline()
        for k in keys:
            pipe.type(k)
            pipe.ttl(k)
        meta = await pipe.execute()
        result = []
        for i, k in enumerate(keys):
            result.append({"key": k, "type": meta[i * 2], "ttl": meta[i * 2 + 1]})
        return result
    finally:
        await r.aclose()


# ── Redis: get key value ──────────────────────────────────────────────────────

@router.get("/redis/keys/{key:path}")
async def redis_get(
    key: str,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> dict:
    await _require_admin(request, session)
    r = aioredis.from_url(
        settings.redis_url, encoding="utf-8", decode_responses=True
    )
    try:
        ktype = await r.type(key)
        if ktype == "string":
            raw = await r.get(key)
            try:
                value = json.loads(raw)  # type: ignore[arg-type]
            except Exception:
                value = raw
        elif ktype == "hash":
            value = await r.hgetall(key)
        elif ktype == "list":
            value = await r.lrange(key, 0, 99)
        elif ktype == "set":
            value = list(await r.smembers(key))
        elif ktype == "zset":
            value = await r.zrange(key, 0, 99, withscores=True)
        else:
            value = None
        ttl = await r.ttl(key)
        return {"key": key, "type": ktype, "ttl": ttl, "value": value}
    finally:
        await r.aclose()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _serialize_row(row: dict) -> dict[str, Any]:
    """Convert non-JSON-serializable types to strings."""
    import datetime, decimal, uuid
    out: dict[str, Any] = {}
    for k, v in row.items():
        if isinstance(v, (datetime.datetime, datetime.date)):
            out[k] = v.isoformat()
        elif isinstance(v, decimal.Decimal):
            out[k] = float(v)
        elif isinstance(v, uuid.UUID):
            out[k] = str(v)
        elif isinstance(v, bytes):
            out[k] = v.hex()
        else:
            out[k] = v
    return out
