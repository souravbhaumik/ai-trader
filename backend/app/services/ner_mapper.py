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

# ── Module-level universe cache ───────────────────────────────────────────────
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
        logger.error("ner_mapper.universe_load_failed", error=str(exc))


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
                    logger.error("ner_mapper.spacy_load_failed", error=str(exc))
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

    # 2. Fallback: whole headline
    candidates.append(headline)

    # 3. Query hint
    if query_hint:
        candidates.append(query_hint)

    symbols: set[str] = set()
    universe_names = list(_UNIVERSE.keys())

    for candidate in candidates:
        if not candidate.strip():
            continue
        match = process.extractOne(
            candidate.lower(),
            universe_names,
            scorer=fuzz.token_set_ratio,
            score_cutoff=82,   # tight threshold to avoid false positives
        )
        if match:
            symbols.add(_UNIVERSE[match[0]])

    return list(symbols)
