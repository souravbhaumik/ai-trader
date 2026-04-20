"""NER + fuzzy symbol mapper — Phase 4.

Extracts organisation names from news headlines using spaCy NER, then
fuzzy-matches them against the ``stock_universe`` company names to produce
a set of NSE ticker symbols.

The universe cache is refreshed from the DB at worker startup and
periodically. A module-level ``_UNIVERSE`` dict is populated once per
process so every task invocation pays zero DB cost.
"""
from __future__ import annotations

import threading
import time
from typing import Optional

import structlog

logger = structlog.get_logger(__name__)

# ── Hardcoded alias map (checked BEFORE fuzzy matching) ──────────────────────
# Keys are lowercase; multiple aliases can map to the same symbol.
# Covers major Indian conglomerates where fuzzy matching is unreliable due to
# shared brand names (e.g. "Reliance" → Industries, Power, Infrastructure).
ALIAS_MAP: dict[str, str] = {
    # Reliance group
    "reliance industries": "RELIANCE",
    "reliance": "RELIANCE",
    "ril": "RELIANCE",
    "jio": "RELIANCE",
    "reliance jio": "RELIANCE",
    "reliance retail": "RELIANCE",
    "reliance power": "RPOWER",
    "reliance infrastructure": "RELINFRA",
    "reliance capital": "RELCAPITAL",
    # Tata group
    "tata consultancy": "TCS",
    "tcs": "TCS",
    "tata motors": "TATAMOTORS",
    "tata steel": "TATASTEEL",
    "tata power": "TATAPOWER",
    "tata consumer": "TATACONSUM",
    "tata chemicals": "TATACHEM",
    "tata communications": "TATACOMM",
    "tata elxsi": "TATAELXSI",
    "titan": "TITAN",
    "tanishq": "TITAN",
    # HDFC group
    "hdfc bank": "HDFCBANK",
    "hdfc life": "HDFCLIFE",
    "hdfc amc": "HDFCAMC",
    "hdfc": "HDFCBANK",
    # Bajaj group
    "bajaj finance": "BAJFINANCE",
    "bajaj finserv": "BAJAJFINSV",
    "bajaj auto": "BAJAJ-AUTO",
    "bajaj": "BAJAJ-AUTO",
    # Adani group
    "adani ports": "ADANIPORTS",
    "adani enterprises": "ADANIENT",
    "adani green": "ADANIGREEN",
    "adani power": "ADANIPOWER",
    "adani total gas": "ATGL",
    "adani transmission": "ADANITRANS",
    "adani wilmar": "AWL",
    "adani": "ADANIENT",
    # Banking / NBFCs
    "state bank": "SBIN",
    "state bank of india": "SBIN",
    "sbi": "SBIN",
    "icici bank": "ICICIBANK",
    "icici prudential": "ICICIPRULI",
    "icici lombard": "ICICIGI",
    "kotak mahindra": "KOTAKBANK",
    "kotak": "KOTAKBANK",
    "axis bank": "AXISBANK",
    "yes bank": "YESBANK",
    "indusind": "INDUSINDBK",
    "federal bank": "FEDERALBNK",
    "bandhan bank": "BANDHANBNK",
    # IT sector
    "infosys": "INFY",
    "wipro": "WIPRO",
    "hcl tech": "HCLTECH",
    "hcl technologies": "HCLTECH",
    "tech mahindra": "TECHM",
    "ltimindtree": "LTIM",
    "l&t infotech": "LTIM",
    "mphasis": "MPHASIS",
    # Auto
    "maruti suzuki": "MARUTI",
    "maruti": "MARUTI",
    "hero motocorp": "HEROMOTOCO",
    "hero honda": "HEROMOTOCO",
    "m&m": "M&M",
    "mahindra": "M&M",
    "eicher motors": "EICHERMOT",
    "royal enfield": "EICHERMOT",
    "tvs motor": "TVSMOTOR",
    # FMCG / Consumer
    "hindustan unilever": "HINDUNILVR",
    "hul": "HINDUNILVR",
    "itc": "ITC",
    "nestle": "NESTLEIND",
    "nestle india": "NESTLEIND",
    "dabur": "DABUR",
    "marico": "MARICO",
    "godrej consumer": "GODREJCP",
    "britannia": "BRITANNIA",
    "emami": "EMAMILTD",
    # Pharma
    "sun pharma": "SUNPHARMA",
    "sun pharmaceutical": "SUNPHARMA",
    "dr reddy": "DRREDDY",
    "dr. reddy": "DRREDDY",
    "cipla": "CIPLA",
    "divi's": "DIVISLAB",
    "divis laboratories": "DIVISLAB",
    "lupin": "LUPIN",
    "aurobindo": "AUROPHARMA",
    # Infra / Metals / Energy
    "larsen & toubro": "LT",
    "l&t": "LT",
    "jsw steel": "JSWSTEEL",
    "hindalco": "HINDALCO",
    "vedanta": "VEDL",
    "coal india": "COALINDIA",
    "ntpc": "NTPC",
    "power grid": "POWERGRID",
    "gail": "GAIL",
    "ongc": "ONGC",
    "oil india": "OIL",
    "ioc": "IOC",
    "indian oil": "IOC",
    "bpcl": "BPCL",
    "bharat petroleum": "BPCL",
    # Telecom
    "bharti airtel": "BHARTIARTL",
    "airtel": "BHARTIARTL",
    "vodafone idea": "IDEA",
    "vi": "IDEA",
    # Consumer tech / new-age
    "zomato": "ZOMATO",
    "nykaa": "NYKAA",
    "paytm": "PAYTM",
    "policybazaar": "POLICYBZR",
    "delhivery": "DELHIVERY",
    # Paints / Speciality
    "asian paints": "ASIANPAINT",
    "pidilite": "PIDILITIND",
    "berger paints": "BERGEPAINT",
    # Others
    "dlf": "DLF",
    "godrej properties": "GODREJPROP",
    "phoenix mills": "PHOENIXLTD",
    "irctc": "IRCTC",
    "hpcl": "HINDPETRO",
}



_UNIVERSE: dict[str, str] = {}   # {company_name_lower: symbol}
_UNIVERSE_LOCK = threading.Lock()
_UNIVERSE_LOADED_AT: float = 0.0
_UNIVERSE_TTL = 3600.0          # reload every hour


def _load_universe() -> None:
    """Pull (symbol, name) pairs from stock_universe into the in-process cache."""
    global _UNIVERSE_LOADED_AT
    from app.core.database import get_sync_session
    from sqlalchemy import text

    try:
        with get_sync_session() as session:
            rows = session.execute(
                text("SELECT symbol, name FROM stock_universe WHERE is_active = TRUE")
            ).fetchall()

        with _UNIVERSE_LOCK:
            _UNIVERSE.clear()
            for symbol, name in rows:
                if name:
                    _UNIVERSE[name.lower()] = symbol
            _UNIVERSE_LOADED_AT = time.monotonic()

        logger.info("ner_mapper.universe_loaded", count=len(_UNIVERSE))
    except Exception as exc:
        logger.error("ner_mapper.universe_load_failed", err=str(exc))


def _ensure_universe() -> None:
    if time.monotonic() - _UNIVERSE_LOADED_AT > _UNIVERSE_TTL or not _UNIVERSE:
        _load_universe()


# ── spaCy model (lazy-loaded, once per process) ───────────────────────────────
_NLP = None
_NLP_LOCK = threading.Lock()


def _get_nlp():
    global _NLP
    if _NLP is None:
        with _NLP_LOCK:
            if _NLP is None:
                try:
                    import spacy
                    _NLP = spacy.load("en_core_web_sm", disable=["parser", "tagger", "lemmatizer"])
                    logger.info("ner_mapper.spacy_loaded")
                except Exception as exc:
                    logger.error("ner_mapper.spacy_load_failed", err=str(exc))
                    _NLP = False   # sentinel — don't retry on every call
    return _NLP if _NLP else None


# ── Public API ────────────────────────────────────────────────────────────────

def map_headline_to_symbols(headline: str, query_hint: Optional[str] = None) -> list[str]:
    """Return a list of NSE symbols mentioned in *headline*.

    Strategy (in priority order):
    1. Named-entity extraction (ORG labels) via spaCy → fuzzy match
    2. Direct fuzzy match of the whole headline against company names
    3. If ``query_hint`` is provided (Google News query), try it as a fallback

    Returns an empty list if no confident match is found.
    """
    _ensure_universe()
    if not _UNIVERSE:
        return []

    try:
        from rapidfuzz import process, fuzz
    except ImportError:
        logger.error("ner_mapper.rapidfuzz_missing")
        return []

    candidates: list[str] = []

    # 1. spaCy NER
    nlp = _get_nlp()
    if nlp:
        doc = nlp(headline)
        candidates += [ent.text for ent in doc.ents if ent.label_ == "ORG"]

    # 2. Query hint
    if query_hint:
        candidates.append(query_hint)

    symbols: set[str] = set()
    universe_names = list(_UNIVERSE.keys())

    for candidate in candidates:
        if not candidate.strip():
            continue
        # ── 1. Alias map (exact, case-insensitive) — highest priority ─────────
        alias_hit = ALIAS_MAP.get(candidate.strip().lower())
        if alias_hit:
            symbols.add(alias_hit)
            continue
        # ── 2. Fuzzy match against DB universe ────────────────────────────────
        match = process.extractOne(
            candidate.lower(),
            universe_names,
            scorer=fuzz.token_set_ratio,
            score_cutoff=82,   # tight threshold to avoid false positives
        )
        if match:
            symbols.add(_UNIVERSE[match[0]])

    return list(symbols)
