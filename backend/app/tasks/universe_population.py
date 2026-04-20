"""Universe population Celery task.

Downloads the NSE equity master list and Nifty index constituents, then
upserts the full stock_universe table.

Data sources (all public, no auth required):
  NSE equity list : https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv
  Nifty 50 list   : https://www.niftyindices.com/IndexConstituent/ind_nifty50list.csv
  Nifty 500 list  : https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv

Run from the admin panel (Setup → Populate Universe) as a one-time step.
Can safely be re-run: uses ON CONFLICT DO UPDATE so it acts as a refresh.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime

import structlog
from sqlalchemy import text

from app.core.database import get_sync_session
from app.tasks.celery_app import celery_app
from app.tasks.task_utils import clear_task_logs, now_iso, write_task_status

logger = structlog.get_logger(__name__)

_TASK_NAME = "universe_population"

# ── Data source URLs (no auth, official NSE/NiftyIndices) ────────────────────
_NSE_EQUITY_URL   = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
_NIFTY50_URL      = "https://www.niftyindices.com/IndexConstituent/ind_nifty50list.csv"
_NIFTY500_URL     = "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}

_INDUSTRY_TO_SECTOR: dict[str, str] = {
    "Financial Services": "Finance",
    "Information Technology": "IT",
    "Fast Moving Consumer Goods": "FMCG",
    "Healthcare": "Pharma",
    "Automobile and Auto Components": "Auto",
    "Capital Goods": "Capital Goods",
    "Construction Materials": "Cement",
    "Metals & Mining": "Metal",
    "Oil Gas & Consumable Fuels": "Energy",
    "Power": "Energy",
    "Telecommunication": "Telecom",
    "Consumer Durables": "Consumer",
    "Realty": "Realty",
    "Media Entertainment & Publication": "Media",
    "Chemicals": "Chemicals",
    "Forest Materials": "Materials",
    "Textiles": "Textiles",
    "Services": "Services",
    "Diversified": "Diversified",
    "Construction": "Infra",
    "Agriculture": "Agri",
}

# Hardcoded Nifty 50 fallback used when the live CSV is unavailable
_NIFTY50_FALLBACK = {
    "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
    "BAJAJ-AUTO", "BAJAJFINSV", "BAJFINANCE", "BHARTIARTL", "BPCL",
    "BRITANNIA", "CIPLA", "COALINDIA", "DIVISLAB", "DRREDDY",
    "EICHERMOT", "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE",
    "HEROMOTOCO", "HINDUNILVR", "HINDALCO", "ICICIBANK", "INDUSINDBK",
    "INFY", "ITC", "JSWSTEEL", "KOTAKBANK", "LT",
    "LTIM", "MM", "MARUTI", "NESTLEIND", "NTPC",
    "ONGC", "POWERGRID", "RELIANCE", "SBILIFE", "SBIN",
    "SHREECEM", "SUNPHARMA", "TATACONSUM", "TATAMOTORS", "TATASTEEL",
    "TCS", "TECHM", "TITAN", "ULTRACEMCO", "UPL", "WIPRO",
}


def _fetch_csv(url: str) -> list[dict]:
    """Fetch a CSV URL and return rows as list of dicts (normalised keys)."""
    import requests

    resp = requests.get(url, headers=_HEADERS, timeout=30)
    resp.raise_for_status()
    text_content = resp.text.lstrip("\ufeff")   # strip BOM
    reader = csv.DictReader(io.StringIO(text_content))
    return [{k.strip(): v.strip() for k, v in row.items()} for row in reader]


def _load_index_metadata() -> tuple[set[str], set[str], dict[str, tuple[str, str]]]:
    """Return (nifty50_syms, nifty500_syms, sym → (sector, industry))."""
    nifty50_syms:  set[str] = set(_NIFTY50_FALLBACK)
    nifty500_syms: set[str] = set()
    meta: dict[str, tuple[str, str]] = {}

    try:
        rows = _fetch_csv(_NIFTY50_URL)
        nifty50_syms = {r.get("Symbol", "").upper() for r in rows if r.get("Symbol")}
        logger.info("universe.nifty50_loaded", count=len(nifty50_syms))
    except Exception as exc:
        logger.warning("universe.nifty50_fallback", err=str(exc))

    try:
        rows = _fetch_csv(_NIFTY500_URL)
        for r in rows:
            sym = r.get("Symbol", "").upper()
            if not sym:
                continue
            nifty500_syms.add(sym)
            industry = r.get("Industry", "")
            sector   = _INDUSTRY_TO_SECTOR.get(industry, industry or "Other")
            meta[sym] = (sector, industry)
        logger.info("universe.nifty500_loaded", count=len(nifty500_syms))
    except Exception as exc:
        logger.warning("universe.nifty500_failed", err=str(exc))

    return nifty50_syms, nifty500_syms, meta


@celery_app.task(name="app.tasks.universe_population.populate_universe")
def populate_universe(nifty500_only: bool = False) -> dict:
    """Download NSE equity master and upsert into stock_universe.

    Args:
        nifty500_only: If True, only insert Nifty 500 stocks (~500 rows, faster).
                       If False, insert the full NSE EQ universe (~2100 rows).
    """
    started = now_iso()
    clear_task_logs(_TASK_NAME)
    write_task_status(
        _TASK_NAME, "running",
        "Downloading NSE equity master list…",
        started_at=started,
    )
    logger.info("universe.start", nifty500_only=nifty500_only)

    # ── Step 1: Load index metadata ──────────────────────────────────────────
    try:
        nifty50_syms, nifty500_syms, meta = _load_index_metadata()
    except Exception as exc:
        msg = f"Failed to load index metadata: {exc}"
        write_task_status(_TASK_NAME, "error", msg, started_at=started, finished_at=now_iso())
        logger.error("universe.metadata_failed", err=str(exc))
        return {"status": "error", "message": msg}

    # ── Step 2: Load full NSE equity list ────────────────────────────────────
    try:
        all_rows = _fetch_csv(_NSE_EQUITY_URL)
        eq_rows  = [r for r in all_rows if r.get("SERIES", "") == "EQ"]
        logger.info("universe.equity_list_loaded", total=len(all_rows), eq=len(eq_rows))
    except Exception as exc:
        msg = f"Failed to download NSE equity list: {exc}"
        write_task_status(_TASK_NAME, "error", msg, started_at=started, finished_at=now_iso())
        logger.error("universe.equity_list_failed", err=str(exc))
        return {"status": "error", "message": msg}

    # ── Step 3: Filter (optionally to Nifty 500 only) ────────────────────────
    if nifty500_only:
        eq_rows = [r for r in eq_rows if r.get("SYMBOL", "").upper() in nifty500_syms]
        logger.info("universe.filtered_nifty500", count=len(eq_rows))

    write_task_status(
        _TASK_NAME, "running",
        f"Upserting {len(eq_rows)} symbols into stock_universe…",
        started_at=started,
    )

    # ── Step 4: Upsert into stock_universe ───────────────────────────────────
    inserted = 0
    updated  = 0
    errors   = 0

    with get_sync_session() as session:
        for row in eq_rows:
            sym  = row.get("SYMBOL", "").upper().strip()
            if not sym:
                continue
            name = row.get("NAME OF COMPANY", sym).strip() or sym

            sector, industry = meta.get(sym, ("Other", ""))
            in50  = sym in nifty50_syms
            in500 = sym in nifty500_syms

            try:
                result = session.execute(
                    text("""
                        INSERT INTO stock_universe
                            (symbol, name, exchange, sector, industry,
                             is_active, in_nifty50, in_nifty500, updated_at)
                        VALUES
                            (:sym, :name, 'NSE', :sector, :industry,
                             TRUE, :in50, :in500, :now)
                        ON CONFLICT (symbol) DO UPDATE SET
                            name      = EXCLUDED.name,
                            sector    = CASE WHEN EXCLUDED.sector != 'Other'
                                             THEN EXCLUDED.sector
                                             ELSE stock_universe.sector END,
                            industry  = CASE WHEN EXCLUDED.industry != ''
                                             THEN EXCLUDED.industry
                                             ELSE stock_universe.industry END,
                            in_nifty50  = EXCLUDED.in_nifty50,
                            in_nifty500 = EXCLUDED.in_nifty500,
                            is_active   = TRUE,
                            updated_at  = EXCLUDED.updated_at
                    """),
                    {
                        "sym":      sym,
                        "name":     name[:255],
                        "sector":   sector,
                        "industry": industry,
                        "in50":     in50,
                        "in500":    in500,
                        "now":      datetime.utcnow(),
                    },
                )
                if result.rowcount > 0:
                    inserted += 1
                else:
                    updated += 1
            except Exception as exc:
                errors += 1
                logger.debug("universe.row_error", symbol=sym, err=str(exc))

        session.commit()

    summary = {
        "total_processed": len(eq_rows),
        "inserted":        inserted,
        "updated":         updated,
        "errors":          errors,
        "nifty500_only":   nifty500_only,
    }
    msg = (
        f"Universe populated: {inserted + updated} symbols upserted "
        f"({inserted} new, {updated} updated, {errors} errors)."
    )
    write_task_status(
        _TASK_NAME, "done", msg,
        started_at=started, finished_at=now_iso(),
        summary=summary,
    )
    logger.info("universe.done", **summary)
    return {"status": "done", **summary}
