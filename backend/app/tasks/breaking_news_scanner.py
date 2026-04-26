"""Breaking news fast-path scanner — Phase 4b.

Runs every 2 minutes during market hours (9:00 AM – 4:30 PM IST Mon–Fri).

Why this exists
---------------
The main ``fetch_news_sentiment`` task runs every 15 minutes.  A major
event (RBI rate change, circuit breaker, earnings miss, SEBI action) announced
at 10:07 AM would not affect signals until 10:15 AM — an 8-minute blind spot
where trades may already be moving against the signal.

This scanner provides a ~2-minute latency path:
  1. Hits 4 fast, free sources — just headline + URL (no body, no FinBERT)
  2. Keyword-matches against a tiered impact vocabulary
  3. If HIGH-impact keywords found → immediately triggers full ``fetch_news_sentiment``
  4. Tracks seen URLs in Redis (``breaking:seen:{url_hash}``) to never re-trigger

No paid API.  All sources are free RSS or public JSON.

Free sources used
-----------------
1. NSE corporate announcements JSON — official exchange feed, symbol pre-mapped
   https://www.nseindia.com/api/corporate-announcements?index=equities
2. BSE corporate announcements RSS — official exchange feed
   https://api.bseindia.com/BseIndiaAPI/api/RssFeed/w?flag=13
3. ET Markets RSS — fastest Indian financial news wire
   https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms
4. Moneycontrol buzzing stocks RSS — real-time market-moving headlines
   https://www.moneycontrol.com/rss/buzzingstocks.xml
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone, timedelta
from typing import Optional

import structlog

from app.tasks.celery_app import celery_app

logger = structlog.get_logger(__name__)

# ── Redis keys ────────────────────────────────────────────────────────────────
_SEEN_PREFIX  = "breaking:seen:"
_SEEN_TTL     = 3600 * 4   # 4 hours — don't re-trigger on the same article
_COOLDOWN_KEY = "breaking:cooldown"
_COOLDOWN_TTL = 120         # 2 minutes — prevent trigger spam if many hits arrive

# ── Fast sources (fetched every tick) ─────────────────────────────────────────
_FAST_RSS = {
    "bse_corp":    "https://api.bseindia.com/BseIndiaAPI/api/RssFeed/w?flag=13",
    "et_markets":  "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "mc_buzzing":  "https://www.moneycontrol.com/rss/buzzingstocks.xml",
}

_NSE_CORP_URL = (
    "https://www.nseindia.com/api/corporate-announcements"
    "?index=equities&from_date=&to_date=&symbol=&issuer=&subject="
)

_RSS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}

# ── Keyword tiers ─────────────────────────────────────────────────────────────
# HIGH  → immediately trigger full sentiment fetch
# MEDIUM → trigger only if 2+ matches in the same scan
_HIGH_IMPACT: frozenset[str] = frozenset({
    # Regulatory / legal
    "sebi", "ban", "suspended", "suspension", "debarred", "penalty", "investigation",
    "fraud", "default", "bankrupt", "insolvency", "irs", "cbi", "ed probe",
    # Exchange actions
    "circuit breaker", "upper circuit", "lower circuit", "trading halt",
    "halt", "delisted", "delisting",
    # Monetary policy
    "rbi", "repo rate", "rate cut", "rate hike", "monetary policy", "crr", "slr",
    "emergency", "liquidity",
    # Major corporate
    "merger", "acquisition", "takeover", "buyout", "open offer",
    "rights issue", "qip", "fpo", "ipo", "buyback",
    # Macro shocks
    "war", "sanction", "tariff", "import ban", "export ban",
    "fii sell", "fpi sell", "sell-off", "selloff", "crash", "collapse",
    "downgrade", "ratings cut",
})

_MEDIUM_IMPACT: frozenset[str] = frozenset({
    "quarterly results", "q1 results", "q2 results", "q3 results", "q4 results",
    "earnings", "profit", "loss", "revenue", "ebitda", "net profit", "net loss",
    "dividend", "bonus shares", "stock split", "face value",
    "board meeting", "agm", "egm",
    "fii", "fpi", "dii", "nri investment",
    "gdp", "inflation", "cpi", "iip",
    "oil price", "crude", "opec",
    "rupee", "usd inr", "dollar",
})


def _url_hash(url: str) -> str:
    return hashlib.md5(url.encode("utf-8", errors="replace")).hexdigest()[:16]


def _is_recent(pub_str: str, max_minutes: int = 30) -> bool:
    """Return True if the article was published within the last max_minutes."""
    try:
        import feedparser as _fp
        import calendar
        ts = _fp._parse_date(pub_str)  # noqa: SLF001
        if ts:
            dt = datetime.fromtimestamp(calendar.timegm(ts), tz=timezone.utc)
            return (datetime.now(timezone.utc) - dt) < timedelta(minutes=max_minutes)
    except Exception:
        pass
    return True  # assume recent if we can't parse


def _keyword_impact(text: str) -> str:
    """Return 'high', 'medium', or 'none' based on keyword matches."""
    lower = text.lower()
    if any(kw in lower for kw in _HIGH_IMPACT):
        return "high"
    medium_hits = sum(1 for kw in _MEDIUM_IMPACT if kw in lower)
    if medium_hits >= 2:
        return "medium"
    return "none"


def _strip_html(s: str) -> str:
    return re.sub(r"<[^>]+>", " ", s).strip()


# ── Source fetchers ───────────────────────────────────────────────────────────

def _fetch_nse_corp_json() -> list[dict]:
    """NSE corporate announcements JSON — structured, symbol pre-mapped, free."""
    try:
        import requests
        resp = requests.get(
            _NSE_CORP_URL,
            headers=_RSS_HEADERS,
            timeout=8,
        )
        resp.raise_for_status()
        data = resp.json()
        items = data if isinstance(data, list) else data.get("data", [])
        results = []
        for item in items[:40]:  # last 40 announcements only
            symbol = (item.get("symbol") or "").upper().strip()
            subject = _strip_html(item.get("subject") or item.get("desc") or "")
            if not subject:
                continue
            results.append({
                "title":   subject,
                "url":     item.get("attchmntFile") or f"nse_corp_{symbol}_{subject[:30]}",
                "source":  "nse_corp_json",
                "symbol":  symbol or None,   # already mapped — no NER needed
            })
        return results
    except Exception as exc:
        logger.debug("breaking_scanner.nse_json_failed", err=str(exc))
        return []


def _fetch_fast_rss() -> list[dict]:
    """Fetch headlines from the 3 fast RSS feeds, return last-30-min articles only."""
    try:
        import feedparser
    except ImportError:
        return []

    results = []
    for source, url in _FAST_RSS.items():
        try:
            feed = feedparser.parse(url, request_headers=_RSS_HEADERS)
            for entry in feed.entries[:20]:  # top 20 per feed
                title = _strip_html(entry.get("title", "")).strip()
                if not title:
                    continue
                link = entry.get("link", "")
                # Only include truly recent articles
                pub_raw = entry.get("published") or entry.get("updated") or ""
                if pub_raw and not _is_recent(pub_raw, max_minutes=30):
                    continue
                results.append({
                    "title":  title,
                    "url":    link or f"{source}:{_url_hash(title)}",
                    "source": source,
                    "symbol": None,
                })
        except Exception as exc:
            logger.debug("breaking_scanner.rss_failed", source=source, err=str(exc))

    return results


# ── Celery task ───────────────────────────────────────────────────────────────

@celery_app.task(
    name="app.tasks.breaking_news_scanner.scan_breaking_news",
    bind=True,
    max_retries=0,       # fire-and-forget — don't retry if a fetch fails
    time_limit=90,       # hard kill after 90s to prevent stacking
    soft_time_limit=75,
)
def scan_breaking_news(self):  # noqa: ANN001
    """Lightweight breaking news scanner.

    Checks 4 free sources for high-impact keywords every 2 minutes.
    No FinBERT inference — keyword matching only.
    When a HIGH-impact headline is found, triggers the full
    ``fetch_news_sentiment`` task for maximum-freshness scoring.
    """
    import redis as _redis
    from app.core.config import settings

    r = _redis.from_url(settings.redis_url, decode_responses=True)

    # ── Cooldown guard — don't trigger more than once per 2 minutes ───────────
    # (prevents cascading re-triggers if many breaking articles appear at once)
    if r.exists(_COOLDOWN_KEY):
        logger.debug("breaking_scanner.cooldown_active")
        return {"status": "cooldown"}

    # ── Fetch headlines from all fast sources ─────────────────────────────────
    articles = _fetch_nse_corp_json() + _fetch_fast_rss()
    if not articles:
        return {"status": "no_articles"}

    # ── Filter: skip articles we've already seen ──────────────────────────────
    new_articles = []
    for art in articles:
        key = _SEEN_PREFIX + _url_hash(art["url"])
        if not r.exists(key):
            new_articles.append(art)
            r.setex(key, _SEEN_TTL, "1")

    if not new_articles:
        logger.debug("breaking_scanner.all_seen", total=len(articles))
        return {"status": "all_seen"}

    # ── Keyword impact scoring ────────────────────────────────────────────────
    high_hits:   list[dict] = []
    medium_hits: list[dict] = []

    for art in new_articles:
        impact = _keyword_impact(art["title"])
        if impact == "high":
            high_hits.append(art)
        elif impact == "medium":
            medium_hits.append(art)

    logger.info(
        "breaking_scanner.scan_done",
        new=len(new_articles),
        high=len(high_hits),
        medium=len(medium_hits),
    )

    # ── Trigger full sentiment fetch on high-impact hits ──────────────────────
    if high_hits:
        # Extract any pre-mapped NSE symbols (from NSE corp JSON source)
        triggered_symbols = list({
            a["symbol"] for a in high_hits if a.get("symbol")
        })

        logger.warning(
            "breaking_scanner.high_impact_trigger",
            count=len(high_hits),
            headlines=[a["title"][:80] for a in high_hits[:5]],
            symbols=triggered_symbols,
        )

        # Trigger full FinBERT pipeline immediately
        from app.tasks.news_sentiment import fetch_news_sentiment  # noqa: PLC0415
        fetch_news_sentiment.apply_async(queue="default", countdown=2)

        # Set cooldown so we don't re-trigger immediately on next tick
        r.setex(_COOLDOWN_KEY, _COOLDOWN_TTL, "1")

        return {
            "status":   "triggered",
            "high":     len(high_hits),
            "medium":   len(medium_hits),
            "symbols":  triggered_symbols,
            "headlines": [a["title"][:80] for a in high_hits[:5]],
        }

    # ── Medium-impact: trigger only if 3+ medium articles in one scan ─────────
    if len(medium_hits) >= 3:
        logger.info(
            "breaking_scanner.medium_cluster_trigger",
            count=len(medium_hits),
        )
        from app.tasks.news_sentiment import fetch_news_sentiment  # noqa: PLC0415
        fetch_news_sentiment.apply_async(queue="default", countdown=5)
        r.setex(_COOLDOWN_KEY, _COOLDOWN_TTL, "1")
        return {"status": "triggered_medium_cluster", "medium": len(medium_hits)}

    return {"status": "no_trigger", "new": len(new_articles)}
