"""Shared NSE Bhavcopy download utilities.

Consolidated NSE archive download logic used by both bhavcopy.py and
eod_ingest.py to avoid duplicating URLs, headers, and parsing code.
"""
from __future__ import annotations

import io
import zipfile
from datetime import date, datetime, timedelta
from typing import Dict, Optional

import structlog

logger = structlog.get_logger(__name__)

# Common browser-like headers required by NSE
NSE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Connection": "keep-alive",
}

# ── URL builders ──────────────────────────────────────────────────────────────

# Format 1: Traditional archive ZIP (used by bhavcopy task)
_NSE_ARCHIVE_BASE = "https://archives.nseindia.com/content/historical/EQUITIES"

# Format 2: Security-wise bhav CSV (used by eod_ingest task)
_NSE_SEC_BHAV_URL = (
    "https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{date}.csv"
)


def bhavcopy_archive_url(trade_date: date) -> str:
    """Build the NSE archive ZIP URL for the given trade date."""
    dd = trade_date.strftime("%d")
    mon = trade_date.strftime("%b").upper()
    yyyy = trade_date.strftime("%Y")
    return f"{_NSE_ARCHIVE_BASE}/{yyyy}/{mon}/cm{dd}{mon}{yyyy}bhav.csv.zip"


def sec_bhav_url(trade_date: datetime) -> str:
    """Build the security-wise bhav CSV URL for the given trade date."""
    date_str = trade_date.strftime("%d%m%Y")
    return _NSE_SEC_BHAV_URL.format(date=date_str)


# ── Download functions ────────────────────────────────────────────────────────


def download_bhavcopy_zip(trade_date: date, timeout: int = 30):
    """Download and parse the traditional Bhavcopy ZIP. Returns a DataFrame or None."""
    import pandas as pd
    import requests

    url = bhavcopy_archive_url(trade_date)

    try:
        resp = requests.get(url, headers=NSE_HEADERS, timeout=timeout)
        if resp.status_code == 404:
            logger.warning("nse_bhavcopy.not_found", url=url)
            return None
        resp.raise_for_status()
    except Exception as exc:
        logger.warning("nse_bhavcopy.request_failed", err=str(exc))
        return None

    try:
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            with zf.open(zf.namelist()[0]) as f:
                df = pd.read_csv(f)
    except Exception as exc:
        logger.warning("nse_bhavcopy.parse_failed", err=str(exc))
        return None

    # Validate: the CSV's internal TIMESTAMP must match the expected trade date
    if "TIMESTAMP" in df.columns and not df.empty:
        try:
            csv_date = pd.to_datetime(df["TIMESTAMP"].iloc[0]).date()
            if csv_date != trade_date:
                logger.warning(
                    "nse_bhavcopy.stale_date",
                    expected=str(trade_date),
                    got=str(csv_date),
                )
                return None
        except Exception:
            pass  # If date parse fails, proceed with the data

    return df


def download_sec_bhav_csv(trade_date: datetime, timeout: int = 30) -> Optional[Dict[str, dict]]:
    """Download the security-wise bhav CSV. Returns symbol→OHLCV dict or None."""
    import httpx
    import pandas as pd

    url = sec_bhav_url(trade_date)

    try:
        with httpx.Client(follow_redirects=True, timeout=timeout) as client:
            # Establish session with NSE homepage first (sets necessary cookies)
            client.get("https://www.nseindia.com/", headers=NSE_HEADERS)
            resp = client.get(url, headers=NSE_HEADERS)

        if resp.status_code != 200:
            logger.info(
                "nse_sec_bhav.not_available",
                date=trade_date.date().isoformat(),
                status=resp.status_code,
            )
            return None

        df = pd.read_csv(io.StringIO(resp.text))
        df.columns = df.columns.str.strip()

        # Keep only equity series
        df = df[df["SERIES"].str.strip() == "EQ"].copy()
        if df.empty:
            return None

        # Parse trade date from the CSV (DATE1 column: e.g. "17-Apr-2025")
        try:
            csv_date = datetime.strptime(
                df["DATE1"].iloc[0].strip(), "%d-%b-%Y"
            ).replace(hour=0, minute=0, second=0, microsecond=0)
        except Exception:
            csv_date = trade_date.replace(hour=0, minute=0, second=0, microsecond=0)

        result: Dict[str, dict] = {}
        for _, row in df.iterrows():
            symbol = str(row["SYMBOL"]).strip()
            try:
                result[symbol] = {
                    "symbol": symbol,
                    "ts": csv_date,
                    "open": float(row["OPEN_PRICE"]),
                    "high": float(row["HIGH_PRICE"]),
                    "low": float(row["LOW_PRICE"]),
                    "close": float(row["CLOSE_PRICE"]),
                    "volume": int(float(row["TTL_TRD_QNTY"])),
                    "source": "nse",
                }
            except Exception:
                pass

        logger.info(
            "nse_sec_bhav.fetched",
            date=csv_date.date().isoformat(),
            symbols=len(result),
        )
        return result

    except Exception as exc:
        logger.warning("nse_sec_bhav.error", date=trade_date.date().isoformat(), err=str(exc))
        return None


def try_sec_bhav_with_lookback(max_lookback_days: int = 5) -> Optional[Dict[str, dict]]:
    """Try sec_bhav for today, then walk back up to max_lookback_days trading days."""
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    for delta in range(max_lookback_days):
        candidate = today - timedelta(days=delta)
        if candidate.weekday() >= 5:  # skip weekends
            continue
        data = download_sec_bhav_csv(candidate)
        if data:
            return data
    return None
