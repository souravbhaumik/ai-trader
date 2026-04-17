"""Populate the stock_universe table with all NSE-listed equities.

Data sources:
  - All NSE EQ stocks : https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv  (~2100 stocks)
  - Nifty 500 metadata: https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv (sector/industry enrichment)
  - Nifty 50  metadata: https://www.niftyindices.com/IndexConstituent/ind_nifty50list.csv  (in_nifty50 flag)

Run inside the backend container:
    python scripts/populate_universe.py

Flags:
    --nifty500-only   Insert only Nifty 500 stocks (500 rows, fast)
    --nifty50-only    Insert only Nifty 50  stocks (50  rows)
"""
from __future__ import annotations

import csv
import io
import os
import sys
from datetime import datetime

import psycopg2

# -- URLs ----------------------------------------------------------------------
NSE_EQUITY_URL   = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
NIFTY50_CSV_URL  = "https://www.niftyindices.com/IndexConstituent/ind_nifty50list.csv"
NIFTY500_CSV_URL = "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}

# Industry -> broad sector
INDUSTRY_TO_SECTOR: dict[str, str] = {
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

# Fallback Nifty 50 set (used when CSV unavailable)
NIFTY50_FALLBACK = {
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


def get_db_conn():
    return psycopg2.connect(
        host=os.environ.get("DB_HOST", "localhost"),
        port=int(os.environ.get("DB_PORT", "5432")),
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
    )


def fetch_csv(url: str) -> list[dict]:
    import httpx
    resp = httpx.get(url, headers=HEADERS, follow_redirects=True, timeout=30)
    resp.raise_for_status()
    # Strip BOM if present
    text = resp.text.lstrip("\ufeff")
    reader = csv.DictReader(io.StringIO(text))
    # Normalise column names (strip whitespace)
    rows = []
    for row in reader:
        rows.append({k.strip(): v.strip() for k, v in row.items()})
    return rows


def load_nifty_metadata() -> tuple[set[str], set[str], dict[str, tuple[str, str]]]:
    """Returns (nifty50_syms, nifty500_syms, sym -> (sector, industry))."""
    nifty50_syms:  set[str] = set(NIFTY50_FALLBACK)
    nifty500_syms: set[str] = set()
    meta: dict[str, tuple[str, str]] = {}

    print("Fetching Nifty 50 metadata from NSE...")
    try:
        rows = fetch_csv(NIFTY50_CSV_URL)
        for r in rows:
            sym = r.get("Symbol", "").upper()
            if sym:
                nifty50_syms.add(sym)
        print(f"  {len(rows)} Nifty 50 stocks.")
    except Exception as exc:
        print(f"  WARNING: {exc} -- using hardcoded fallback.")

    print("Fetching Nifty 500 metadata from NSE...")
    try:
        rows = fetch_csv(NIFTY500_CSV_URL)
        for r in rows:
            sym = r.get("Symbol", "").upper()
            if not sym:
                continue
            nifty500_syms.add(sym)
            industry = r.get("Industry", "")
            sector   = INDUSTRY_TO_SECTOR.get(industry, industry or "Other")
            meta[sym] = (sector, industry)
        print(f"  {len(rows)} Nifty 500 stocks.")
    except Exception as exc:
        print(f"  WARNING: {exc}")

    return nifty50_syms, nifty500_syms, meta


def load_all_nse_equities() -> list[dict]:
    """Fetch full NSE equity list (~2100 EQ-series stocks)."""
    print("Fetching all NSE-listed equities from NSE archives...")
    try:
        rows = fetch_csv(NSE_EQUITY_URL)
        # Keep only regular EQ series (exclude BE=trade-to-trade, BZ=suspended/Z-group)
        eq_rows = [r for r in rows if r.get("SERIES", "") == "EQ"]
        print(f"  Total: {len(rows)} rows, EQ series: {len(eq_rows)} stocks.")
        return eq_rows
    except Exception as exc:
        print(f"  ERROR fetching NSE equity list: {exc}")
        return []


def upsert_all(cur, equity_rows: list[dict],
               nifty50_syms: set[str], nifty500_syms: set[str],
               meta: dict[str, tuple[str, str]]) -> int:
    now = datetime.utcnow()
    count = 0
    for row in equity_rows:
        sym  = row.get("SYMBOL", "").upper().strip()
        if not sym:
            continue
        name = row.get("NAME OF COMPANY", sym).strip() or sym

        sector, industry = meta.get(sym, ("Other", ""))
        in50  = sym in nifty50_syms
        in500 = sym in nifty500_syms

        cur.execute("""
            INSERT INTO stock_universe
                (symbol, name, exchange, sector, industry,
                 is_active, in_nifty50, in_nifty500, updated_at)
            VALUES (%s, %s, 'NSE', %s, %s, TRUE, %s, %s, %s)
            ON CONFLICT (symbol) DO UPDATE SET
                name        = EXCLUDED.name,
                sector      = CASE WHEN EXCLUDED.sector != 'Other'
                                   THEN EXCLUDED.sector
                                   ELSE stock_universe.sector END,
                industry    = CASE WHEN EXCLUDED.industry != ''
                                   THEN EXCLUDED.industry
                                   ELSE stock_universe.industry END,
                in_nifty50  = EXCLUDED.in_nifty50,
                in_nifty500 = EXCLUDED.in_nifty500,
                is_active   = TRUE,
                updated_at  = EXCLUDED.updated_at
        """, (sym, name, sector, industry, in50, in500, now))
        count += 1
    return count


def upsert_nifty_subset(cur, rows: list[dict],
                        nifty50_syms: set[str], nifty500_syms: set[str]) -> int:
    """Used for --nifty500-only and --nifty50-only modes."""
    now = datetime.utcnow()
    count = 0
    for row in rows:
        sym      = row.get("Symbol", "").upper().strip()
        if not sym:
            continue
        name     = row.get("Company Name", sym).strip() or sym
        industry = row.get("Industry", "").strip()
        sector   = INDUSTRY_TO_SECTOR.get(industry, industry or "Other")
        in50     = sym in nifty50_syms
        in500    = sym in nifty500_syms

        cur.execute("""
            INSERT INTO stock_universe
                (symbol, name, exchange, sector, industry,
                 is_active, in_nifty50, in_nifty500, updated_at)
            VALUES (%s, %s, 'NSE', %s, %s, TRUE, %s, %s, %s)
            ON CONFLICT (symbol) DO UPDATE SET
                name        = EXCLUDED.name,
                sector      = EXCLUDED.sector,
                industry    = EXCLUDED.industry,
                in_nifty50  = EXCLUDED.in_nifty50,
                in_nifty500 = EXCLUDED.in_nifty500,
                is_active   = TRUE,
                updated_at  = EXCLUDED.updated_at
        """, (sym, name, sector, industry, in50, in500, now))
        count += 1
    return count


def main():
    mode = "all"
    if "--nifty50-only" in sys.argv:
        mode = "nifty50"
    elif "--nifty500-only" in sys.argv:
        mode = "nifty500"

    print("Connecting to database...")
    conn = get_db_conn()
    cur  = conn.cursor()

    nifty50_syms, nifty500_syms, meta = load_nifty_metadata()

    if mode == "nifty50":
        rows = fetch_csv(NIFTY50_CSV_URL)
        print(f"Upserting {len(rows)} Nifty 50 stocks...")
        total = upsert_nifty_subset(cur, rows, nifty50_syms, nifty500_syms)

    elif mode == "nifty500":
        rows = fetch_csv(NIFTY500_CSV_URL)
        print(f"Upserting {len(rows)} Nifty 500 stocks...")
        total = upsert_nifty_subset(cur, rows, nifty50_syms, nifty500_syms)

    else:  # all NSE equities
        equity_rows = load_all_nse_equities()
        if not equity_rows:
            print("ERROR: Could not fetch NSE equity list. Aborting.")
            sys.exit(1)
        print(f"Upserting {len(equity_rows)} NSE equities...")
        total = upsert_all(cur, equity_rows, nifty50_syms, nifty500_syms, meta)

    conn.commit()
    cur.close()
    conn.close()

    print(f"Done -- {total} stocks upserted into stock_universe.")
    print(f"  Nifty 50  : {len(nifty50_syms)} symbols tagged")
    print(f"  Nifty 500 : {len(nifty500_syms)} symbols tagged")


if __name__ == "__main__":
    missing = [v for v in ("DB_NAME", "DB_USER", "DB_PASSWORD") if not os.environ.get(v)]
    if missing:
        print(f"ERROR: Missing env vars: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)
    main()
