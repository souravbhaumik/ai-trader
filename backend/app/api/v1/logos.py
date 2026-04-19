"""Logo endpoint — serves pre-downloaded company logos.

Flow:
  1. Look up logo_path in stock_universe (set by download_logos.py script).
  2. If logo_path is set and the file exists → serve the PNG directly.
  3. Otherwise → return a deterministic SVG avatar (letter + colour).

No live network calls are made here. All logos are pre-fetched once via
the download_logos.py script and stored at app/static/logos/{SYMBOL}.png.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, Response
from sqlalchemy import text

from app.core.database import get_sync_session

router = APIRouter(prefix="/logos", tags=["logos"])

_PALETTE = [
    "#3b82f6", "#8b5cf6", "#ec4899", "#f97316",
    "#10b981", "#06b6d4", "#f59e0b", "#ef4444",
    "#6366f1", "#14b8a6",
]

_CACHE_HEADERS = {"Cache-Control": "public, max-age=86400"}  # 24 h — only for real PNGs
_NO_CACHE       = {"Cache-Control": "no-store"}               # for SVG fallbacks


def _svg_avatar(ticker: str) -> bytes:
    label = ticker[:2] if len(ticker) > 2 else ticker
    color = _PALETTE[int(hashlib.md5(ticker.encode()).hexdigest(), 16) % len(_PALETTE)]
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64">'
        f'<rect width="64" height="64" rx="10" fill="{color}"/>'
        f'<text x="32" y="32" dy=".35em" text-anchor="middle" '
        f'font-family="monospace" font-weight="700" font-size="24" fill="#fff">'
        f"{label}</text></svg>"
    )
    return svg.encode()


@router.get("/{symbol}")
def get_logo(symbol: str) -> Response:
    ticker = (
        symbol.upper()
        .replace(".NS", "").replace(".BO", "").replace(".BSE", "")
    )

    # ── 1. Look up logo_path in DB ────────────────────────────────────────────
    with get_sync_session() as session:
        row = session.execute(
            text("SELECT logo_path FROM stock_universe WHERE symbol = :s OR symbol = :ns"),
            {"s": ticker, "ns": f"{ticker}.NS"},
        ).fetchone()

    logo_path: str | None = row[0] if row else None

    # ── 2. Serve cached PNG ───────────────────────────────────────────────────
    if logo_path:
        p = Path(logo_path)
        if p.exists():
            return FileResponse(str(p), media_type="image/png", headers=_CACHE_HEADERS)

    # ── 3. SVG avatar fallback (not cached — logo may be downloaded later) ────
    return Response(
        content=_svg_avatar(ticker),
        media_type="image/svg+xml",
        headers=_NO_CACHE,
    )
