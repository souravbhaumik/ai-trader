"""Celery task: bulk-download ticker logos from logo.dev and cache locally.

Queue : celery  (default, CPU-light I/O task)
Safe  : fully incremental — symbols whose PNG already exists are skipped.
Logs  : written to Redis pipeline:logs:logo_download (visible in Admin UI).
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import httpx
import structlog

from app.tasks.celery_app import celery_app
from app.tasks.task_utils import append_task_log, write_task_status

logger = structlog.get_logger(__name__)

TASK_NAME  = "logo_download"
LOGOS_DIR  = Path(__file__).parents[2] / "static" / "logos"
LOGO_URL   = "https://img.logo.dev/{domain}?token={token}&size=64&format=png&fallback=404"

# ── Known NSE ticker → domain overrides (same map as download_logos.py script) ──
_DOMAIN: dict[str, str] = {
    "HDFCBANK": "hdfcbank.com", "ICICIBANK": "icicibank.com", "SBIN": "sbi.co.in",
    "KOTAKBANK": "kotak.com", "AXISBANK": "axisbank.com", "INDUSINDBK": "indusind.com",
    "BANDHANBNK": "bandhanbank.com", "FEDERALBNK": "federalbank.co.in",
    "IDFCFIRSTB": "idfcfirstbank.com", "PNB": "pnbindia.in", "CANBK": "canarabank.in",
    "UNIONBANK": "unionbankofindia.co.in", "BANKBARODA": "bankofbaroda.in",
    "AUBANK": "aubank.in", "RBLBANK": "rblbank.com", "YESBANK": "yesbank.in",
    "IDBI": "idbi.com", "IOB": "iob.in",
    "TCS": "tcs.com", "INFY": "infosys.com", "WIPRO": "wipro.com",
    "HCLTECH": "hcltech.com", "TECHM": "techmahindra.com", "LTIM": "ltimindtree.com",
    "MPHASIS": "mphasis.com", "PERSISTENT": "persistent.com", "COFORGE": "coforge.com",
    "TATAELXSI": "tataelxsi.com", "KPITTECH": "kpit.com", "ZENSAR": "zensar.com",
    "LTTS": "ltts.com", "HEXAWARE": "hexaware.com", "MASTEK": "mastek.com",
    "INTELLECT": "intellectdesign.com",
    "HINDUNILVR": "hul.co.in", "ITC": "itcportal.com", "NESTLEIND": "nestle.in",
    "BRITANNIA": "britannia.co.in", "DABUR": "dabur.com", "MARICO": "marico.com",
    "GODREJCP": "godrejcp.com", "COLPAL": "colgate.co.in", "VBL": "varunbeverages.com",
    "EMAMILTD": "emamigroup.com", "TATACONSUM": "tatanutrisci.com",
    "RADICO": "radicokhaitan.com",
    "SUNPHARMA": "sunpharma.com", "DRREDDY": "drreddys.com", "CIPLA": "cipla.com",
    "DIVISLAB": "divislabs.com", "AUROPHARMA": "aurobindo.com", "LUPIN": "lupin.com",
    "TORNTPHARM": "torrentpharma.com", "BIOCON": "biocon.com", "ALKEM": "alkemlab.com",
    "IPCALAB": "ipca.com", "GLAND": "glandpharma.com", "SYNGENE": "syngeneintl.com",
    "LALPATHLAB": "lalpathlabs.com", "METROPOLIS": "metropolisindia.com",
    "NATCOPHARM": "natcopharma.com", "GRANULES": "granulesindia.com",
    "BAJFINANCE": "bajajfinserv.in", "BAJAJFINSV": "bajajfinserv.in",
    "HDFCLIFE": "hdfclife.com", "SBILIFE": "sbilife.co.in",
    "ICICIGI": "icicilombard.com", "ICICIPRULI": "iciciprulife.com",
    "MUTHOOTFIN": "muthootfinance.com", "CHOLAFIN": "cholamandalam.com",
    "LICHSGFIN": "lichousing.com", "SBICARD": "sbicard.com",
    "ABCAPITAL": "adityabirlacapital.com", "MANAPPURAM": "manappuram.com",
    "IIFL": "iifl.com",
    "RELIANCE": "ril.com", "ONGC": "ongcindia.com", "IOC": "iocl.com",
    "BPCL": "bharatpetroleum.com", "HPCL": "hindustanpetroleum.com",
    "GAIL": "gail.nic.in", "IGL": "igl.co.in", "MGL": "mahanagargas.com",
    "PETRONET": "petronetlng.com", "TATAPOWER": "tatapower.com",
    "POWERGRID": "powergridindia.com", "NTPC": "ntpc.co.in",
    "ADANIGREEN": "adanigreenenergy.in", "ADANIPORTS": "adaniports.com",
    "ADANIENT": "adani.com", "ADANIPOWER": "adani.com",
    "CESC": "cesc.co.in", "TORNTPOWER": "torrentpower.com",
    "MARUTI": "marutisuzuki.com", "TATAMOTORS": "tatamotors.com",
    "HEROMOTOCO": "heromotocorp.com", "EICHERMOT": "royalenfield.com",
    "TVSMOTOR": "tvsmotor.com", "ASHOKLEY": "ashokleyland.com",
    "MRF": "mrftyres.com", "CEATLTD": "ceat.com", "APOLLOTYRE": "apollotyres.com",
    "MOTHERSON": "motherson.com", "BOSCHLTD": "bosch.in",
    "BHARATFORG": "bharatforge.com", "EXIDEIND": "exide.in",
    "TATASTEEL": "tatasteel.com", "JSWSTEEL": "jsw.in", "SAIL": "sail.co.in",
    "HINDALCO": "hindalco.com", "VEDL": "vedantalimited.com",
    "COALINDIA": "coalindia.in", "NATIONALUM": "nalcoindia.com",
    "NMDC": "nmdc.co.in",
    "ULTRACEMCO": "ultratechcement.com", "ACC": "acclimited.com",
    "AMBUJACEM": "ambujacement.com", "SHREECEM": "shreecement.com",
    "GRASIM": "grasim.com", "JKCEMENT": "jkcement.com",
    "ASIANPAINT": "asianpaints.com", "BERGEPAINT": "bergerpaints.com",
    "PIDILITIND": "pidilite.com", "UPL": "upl-ltd.com",
    "DEEPAKNTR": "deepaknitrite.com", "PIIND": "piindustries.com",
    "BHARTIARTL": "airtel.in", "ZOMATO": "zomato.com", "NYKAA": "nykaa.com",
    "PAYTM": "paytm.com", "DELHIVERY": "delhivery.com",
    "IRCTC": "irctc.co.in", "INDIGO": "goindigo.in", "JIOFIN": "jiofin.com",
    "TITAN": "titancompany.in", "DMART": "dmartindia.com", "TRENT": "trent.co.in",
    "HAVELLS": "havells.com", "VOLTAS": "voltas.com", "BATAINDIA": "bata.in",
    "JUBLFOOD": "jubilantfoodworks.com", "UBL": "unitedbreweries.com",
    "DLF": "dlf.in", "GODREJPROP": "godrejproperties.com",
    "OBEROIRLTY": "oberoirealty.com", "PHOENIXLTD": "phoenixmalls.com",
    "LT": "larsentoubro.com", "SIEMENS": "siemens.co.in",
    "ABB": "abb.com", "BHEL": "bhel.com", "HAL": "hal-india.co.in",
    "BEL": "bel-india.com", "DIXON": "dixontechnologies.com",
    "CGPOWER": "cg.com", "THERMAX": "thermaxglobal.com",
    "BAJAJ_AUTO": "bajajauto.com", "PAGEIND": "jockeyindia.com",
    "MCDOWELL-N": "unitedspirits.in",
}


def _domain_candidates(ticker: str) -> list[str]:
    if ticker in _DOMAIN:
        return [_DOMAIN[ticker]]
    t = ticker.lower().replace("&", "").replace("-", "").replace("_", "")
    return [f"{t}.com", f"{t}.co.in", f"{t}.in"]


async def _fetch_logo(ticker: str, client: httpx.AsyncClient, token: str) -> bytes | None:
    for domain in _domain_candidates(ticker):
        url = LOGO_URL.format(domain=domain, token=token)
        try:
            r = await client.get(url, follow_redirects=True)
            if r.status_code == 200 and len(r.content) > 500:
                return r.content
        except Exception:
            pass
    return None


async def _run_download(token: str) -> dict:
    """Async inner implementation — runs inside asyncio.run() from the Celery task."""
    import psycopg2

    LOGOS_DIR.mkdir(parents=True, exist_ok=True)

    from app.core.config import settings
    conn = psycopg2.connect(
        dbname=settings.db_name,
        user=settings.db_user,
        password=settings.db_password,
        host=settings.db_host,
        port=settings.db_port,
    )
    cur = conn.cursor()

    cur.execute("SELECT symbol FROM stock_universe WHERE is_active = TRUE ORDER BY symbol")
    symbols: list[str] = [row[0] for row in cur.fetchall()]
    total = len(symbols)

    append_task_log(TASK_NAME, f"Found {total} active symbols. Starting download…")

    ok = skip = fail = 0
    sem = asyncio.Semaphore(10)

    async def process(ticker: str) -> None:
        nonlocal ok, skip, fail
        raw = ticker.upper().replace(".NS", "").replace(".BO", "").replace(".BSE", "")
        png_path = LOGOS_DIR / f"{raw}.png"

        if png_path.exists():
            cur.execute(
                "UPDATE stock_universe SET logo_path = %s WHERE symbol = %s",
                (str(png_path), ticker),
            )
            ok += 1
            return

        async with sem:
            png_data = await _fetch_logo(raw, client, token)

        if png_data:
            png_path.write_bytes(png_data)
            cur.execute(
                "UPDATE stock_universe SET logo_path = %s WHERE symbol = %s",
                (str(png_path), ticker),
            )
            ok += 1
        else:
            skip += 1

    async with httpx.AsyncClient(timeout=10.0) as client:
        batch_size = 50
        for i in range(0, total, batch_size):
            batch = symbols[i : i + batch_size]
            await asyncio.gather(*[process(s) for s in batch])
            conn.commit()
            done = min(i + batch_size, total)
            append_task_log(
                TASK_NAME,
                f"Progress {done}/{total} — downloaded={ok}, no-logo={skip}",
            )

    conn.commit()
    cur.close()
    conn.close()

    summary = {"downloaded": ok, "skipped_no_logo": skip, "total": total}
    append_task_log(
        TASK_NAME,
        f"Done. downloaded={ok}  no-logo={skip}  total={total}",
    )
    return summary


@celery_app.task(
    name="app.tasks.download_logos.download_logos",
    bind=True,
    max_retries=1,
    default_retry_delay=120,
)
def download_logos(self) -> dict:  # type: ignore[override]
    """Download and cache logos for all active symbols from logo.dev."""
    from app.core.config import settings

    write_task_status(TASK_NAME, "running", "Logo download in progress…")
    append_task_log(TASK_NAME, "Task started.")

    try:
        if not settings.logo_dev_token:
            raise RuntimeError("LOGO_DEV_TOKEN is not configured.")

        summary = asyncio.run(_run_download(settings.logo_dev_token))

        write_task_status(
            TASK_NAME, "done",
            f"Logo download complete. downloaded={summary['downloaded']} no-logo={summary['skipped_no_logo']}",
            summary=summary,
        )
        logger.info("download_logos.done", **summary)
        return summary

    except Exception as exc:
        write_task_status(TASK_NAME, "error", str(exc))
        append_task_log(TASK_NAME, f"ERROR: {exc}", level="error")
        logger.exception("download_logos.failed", err=str(exc))
        raise self.retry(exc=exc)
