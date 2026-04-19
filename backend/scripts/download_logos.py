"""Bulk download logos for all symbols in stock_universe from logo.dev.

Reads every symbol from the DB, attempts to fetch a PNG from logo.dev,
saves it to app/static/logos/{SYMBOL}.png, and writes the local path back
into stock_universe.logo_path.

Symbols with no logo on logo.dev are skipped (logo_path stays NULL).
The API endpoint serves the PNG if logo_path is set, SVG avatar otherwise.

Usage (from inside Docker):
    docker compose exec backend python /app/scripts/download_logos.py

Env vars required:
    LOGO_DEV_TOKEN  — logo.dev public token (pk_...)
    DB_USER / DB_PASSWORD / DB_HOST / DB_PORT / DB_NAME
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from urllib.parse import quote_plus

import httpx
import psycopg2

# ── Paths ─────────────────────────────────────────────────────────────────────
LOGOS_DIR = Path(__file__).parent.parent / "app" / "static" / "logos"
LOGOS_DIR.mkdir(parents=True, exist_ok=True)

# ── logo.dev ──────────────────────────────────────────────────────────────────
TOKEN = os.environ.get("LOGO_DEV_TOKEN", "")
LOGO_URL = "https://img.logo.dev/{domain}?token={token}&size=64&format=png&fallback=404"

# ── Known NSE ticker → domain mappings ────────────────────────────────────────
_DOMAIN: dict[str, str] = {
    # Banks
    "HDFCBANK": "hdfcbank.com", "ICICIBANK": "icicibank.com", "SBIN": "sbi.co.in",
    "KOTAKBANK": "kotak.com", "AXISBANK": "axisbank.com", "INDUSINDBK": "indusind.com",
    "BANDHANBNK": "bandhanbank.com", "FEDERALBNK": "federalbank.co.in",
    "IDFCFIRSTB": "idfcfirstbank.com", "PNB": "pnbindia.in", "CANBK": "canarabank.in",
    "UNIONBANK": "unionbankofindia.co.in", "BANKBARODA": "bankofbaroda.in",
    "AUBANK": "aubank.in", "RBLBANK": "rblbank.com", "YESBANK": "yesbank.in",
    "IDBI": "idbi.com", "IOB": "iob.in",
    # IT
    "TCS": "tcs.com", "INFY": "infosys.com", "WIPRO": "wipro.com",
    "HCLTECH": "hcltech.com", "TECHM": "techmahindra.com", "LTIM": "ltimindtree.com",
    "MPHASIS": "mphasis.com", "PERSISTENT": "persistent.com", "COFORGE": "coforge.com",
    "TATAELXSI": "tataelxsi.com", "KPITTECH": "kpit.com", "ZENSAR": "zensar.com",
    "LTTS": "ltts.com", "HEXAWARE": "hexaware.com", "MASTEK": "mastek.com",
    "INTELLECT": "intellectdesign.com",
    # FMCG
    "HINDUNILVR": "hul.co.in", "ITC": "itcportal.com", "NESTLEIND": "nestle.in",
    "BRITANNIA": "britannia.co.in", "DABUR": "dabur.com", "MARICO": "marico.com",
    "GODREJCP": "godrejcp.com", "COLPAL": "colgate.co.in", "VBL": "varunbeverages.com",
    "EMAMILTD": "emamigroup.com", "TATACONSUM": "tatanutrisci.com",
    "RADICO": "radicokhaitan.com",
    # Pharma
    "SUNPHARMA": "sunpharma.com", "DRREDDY": "drreddys.com", "CIPLA": "cipla.com",
    "DIVISLAB": "divislabs.com", "AUROPHARMA": "aurobindo.com", "LUPIN": "lupin.com",
    "TORNTPHARM": "torrentpharma.com", "BIOCON": "biocon.com", "ALKEM": "alkemlab.com",
    "IPCALAB": "ipca.com", "GLAND": "glandpharma.com", "SYNGENE": "syngeneintl.com",
    "LALPATHLAB": "lalpathlabs.com", "METROPOLIS": "metropolisindia.com",
    "NATCOPHARM": "natcopharma.com", "GRANULES": "granulesindia.com",
    # Finance / NBFC
    "BAJFINANCE": "bajajfinserv.in", "BAJAJFINSV": "bajajfinserv.in",
    "HDFCLIFE": "hdfclife.com", "SBILIFE": "sbilife.co.in",
    "ICICIGI": "icicilombard.com", "ICICIPRULI": "iciciprulife.com",
    "MUTHOOTFIN": "muthootfinance.com", "CHOLAFIN": "cholamandalam.com",
    "LICHSGFIN": "lichousing.com", "SBICARD": "sbicard.com",
    "ABCAPITAL": "adityabirlacapital.com", "MANAPPURAM": "manappuram.com",
    "IIFL": "iifl.com",
    # Energy / Power
    "RELIANCE": "ril.com", "ONGC": "ongcindia.com", "IOC": "iocl.com",
    "BPCL": "bharatpetroleum.com", "HPCL": "hindustanpetroleum.com",
    "GAIL": "gail.nic.in", "IGL": "igl.co.in", "MGL": "mahanagargas.com",
    "PETRONET": "petronetlng.com", "TATAPOWER": "tatapower.com",
    "POWERGRID": "powergridindia.com", "NTPC": "ntpc.co.in",
    "ADANIGREEN": "adanigreenenergy.in", "ADANIPORTS": "adaniports.com",
    "ADANIENT": "adani.com", "ADANIPOWER": "adani.com",
    "CESC": "cesc.co.in", "TORNTPOWER": "torrentpower.com",
    # Auto
    "MARUTI": "marutisuzuki.com", "TATAMOTORS": "tatamotors.com",
    "HEROMOTOCO": "heromotocorp.com", "EICHERMOT": "royalenfield.com",
    "TVSMOTOR": "tvsmotor.com", "ASHOKLEY": "ashokleyland.com",
    "MRF": "mrftyres.com", "CEATLTD": "ceat.com", "APOLLOTYRE": "apollotyres.com",
    "MOTHERSON": "motherson.com", "BOSCHLTD": "bosch.in",
    "BHARATFORG": "bharatforge.com", "EXIDEIND": "exide.in",
    # Steel / Metals
    "TATASTEEL": "tatasteel.com", "JSWSTEEL": "jsw.in", "SAIL": "sail.co.in",
    "HINDALCO": "hindalco.com", "VEDL": "vedantalimited.com",
    "COALINDIA": "coalindia.in", "NATIONALUM": "nalcoindia.com",
    "NMDC": "nmdc.co.in",
    # Cement
    "ULTRACEMCO": "ultratechcement.com", "ACC": "acclimited.com",
    "AMBUJACEM": "ambujacement.com", "SHREECEM": "shreecement.com",
    "GRASIM": "grasim.com", "JKCEMENT": "jkcement.com",
    # Paint / Chemicals
    "ASIANPAINT": "asianpaints.com", "BERGEPAINT": "bergerpaints.com",
    "PIDILITIND": "pidilite.com", "UPL": "upl-ltd.com",
    "DEEPAKNTR": "deepaknitrite.com", "PIIND": "piindustries.com",
    # Telecom / New Age
    "BHARTIARTL": "airtel.in", "ZOMATO": "zomato.com", "NYKAA": "nykaa.com",
    "PAYTM": "paytm.com", "DELHIVERY": "delhivery.com",
    "IRCTC": "irctc.co.in", "INDIGO": "goindigo.in", "JIOFIN": "jiofin.com",
    # Consumer / Retail
    "TITAN": "titancompany.in", "DMART": "dmartindia.com", "TRENT": "trent.co.in",
    "HAVELLS": "havells.com", "VOLTAS": "voltas.com", "BATAINDIA": "bata.in",
    "JUBLFOOD": "jubilantfoodworks.com", "UBL": "unitedbreweries.com",
    # Realty
    "DLF": "dlf.in", "GODREJPROP": "godrejproperties.com",
    "OBEROIRLTY": "oberoirealty.com", "PHOENIXLTD": "phoenixmalls.com",
    # Industrial / Capital Goods
    "LT": "larsentoubro.com", "SIEMENS": "siemens.co.in",
    "ABB": "abb.com", "BHEL": "bhel.com", "HAL": "hal-india.co.in",
    "BEL": "bel-india.com", "DIXON": "dixontechnologies.com",
    "CGPOWER": "cg.com", "THERMAX": "thermaxglobal.com",
    # Index / misc
    "BAJAJ_AUTO": "bajajauto.com", "PAGEIND": "jockeyindia.com",
    "MCDOWELL-N": "unitedspirits.in",
}


def _domain_candidates(ticker: str) -> list[str]:
    """Return ordered list of domains to try for a ticker."""
    if ticker in _DOMAIN:
        return [_DOMAIN[ticker]]
    t = ticker.lower().replace("&", "").replace("-", "").replace("_", "")
    return [f"{t}.com", f"{t}.co.in", f"{t}.in"]


def _db_connect() -> psycopg2.extensions.connection:
    return psycopg2.connect(
        dbname=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        host=os.environ.get("DB_HOST", "localhost"),
        port=int(os.environ.get("DB_PORT", 5432)),
    )


async def _fetch_logo(ticker: str, client: httpx.AsyncClient) -> bytes | None:
    """Try each domain candidate; return PNG bytes on first hit, None otherwise."""
    for domain in _domain_candidates(ticker):
        url = LOGO_URL.format(domain=domain, token=TOKEN)
        try:
            r = await client.get(url, follow_redirects=True)
            if r.status_code == 200 and len(r.content) > 500:
                return r.content
        except Exception:
            pass
    return None


async def run() -> None:
    if not TOKEN:
        print("ERROR: LOGO_DEV_TOKEN env var is not set. Aborting.")
        sys.exit(1)

    conn = _db_connect()
    cur = conn.cursor()

    cur.execute("SELECT symbol FROM stock_universe WHERE is_active = TRUE ORDER BY symbol")
    symbols: list[str] = [row[0] for row in cur.fetchall()]
    total = len(symbols)
    print(f"Found {total} active symbols. Starting download...")

    ok = skip = fail = 0
    # Throttle: 10 concurrent requests to stay within logo.dev rate limits
    sem = asyncio.Semaphore(10)

    async def process(ticker: str) -> None:
        nonlocal ok, skip, fail
        raw = ticker.upper().replace(".NS", "").replace(".BO", "").replace(".BSE", "")
        png_path = LOGOS_DIR / f"{raw}.png"

        # Already cached — just update DB path
        if png_path.exists():
            cur.execute(
                "UPDATE stock_universe SET logo_path = %s WHERE symbol = %s",
                (str(png_path), ticker),
            )
            ok += 1
            return

        async with sem:
            png_data = await _fetch_logo(raw, client)

        if png_data:
            png_path.write_bytes(png_data)
            cur.execute(
                "UPDATE stock_universe SET logo_path = %s WHERE symbol = %s",
                (str(png_path), ticker),
            )
            ok += 1
            print(f"  ✓ {raw}")
        else:
            # No logo found — leave logo_path NULL
            skip += 1

    async with httpx.AsyncClient(timeout=10.0) as client:
        tasks = [process(s) for s in symbols]
        # Process in batches of 50 to avoid overwhelming memory / connections
        batch_size = 50
        for i in range(0, len(tasks), batch_size):
            await asyncio.gather(*tasks[i : i + batch_size])
            conn.commit()
            done = min(i + batch_size, total)
            print(f"Progress: {done}/{total}  (ok={ok}, skip={skip})")

    conn.commit()
    cur.close()
    conn.close()
    print(f"\nDone. Downloaded={ok}  Skipped(no logo)={skip}  Errors={fail}")


if __name__ == "__main__":
    asyncio.run(run())
