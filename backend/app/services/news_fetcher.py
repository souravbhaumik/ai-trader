"""News fetcher service — Phase 4.

Fetches headlines from multiple free sources and returns them as a flat
list of dicts ready for NER + FinBERT scoring.

Sources
-------
* RSS feeds  — ET Markets, Moneycontrol, Business Standard, Livemint,
               NSE (corporate announcements), BSE (corporate announcements)
* Google News — keyword search via ``gnews``

Each article dict has these keys:
    title       str        Headline text
    url         str | None Full URL
    published   datetime   UTC-normalised publication time
    source      str        Short source identifier
"""
from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# ── RSS feed catalogue ────────────────────────────────────────────────────────
_RSS_FEEDS: dict[str, str] = {
    "et_markets":     "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
    "moneycontrol":   "https://www.moneycontrol.com/rss/latestnews.xml",
    "business_std":   "https://www.business-standard.com/rss/markets-106.rss",
    "livemint":       "https://www.livemint.com/rss/markets",
    "nse_corp":       "https://www.nseindia.com/corporates/rss/allAnnouncements.xml",
    "bse_corp":       "https://api.bseindia.com/BseIndiaAPI/api/RssFeed/w?flag=13",
}

# Max hours in the past to consider an article "fresh"
_MAX_AGE_HOURS = 24


def _parse_dt(raw: Any) -> datetime | None:
    """Convert a feedparser time_struct or ISO string to a UTC datetime."""
    if raw is None:
        return None
    try:
        import time as _time
        if hasattr(raw, "tm_year"):                  # feedparser time_struct
            ts = _time.mktime(raw)
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        return datetime.fromisoformat(str(raw)).astimezone(timezone.utc)
    except Exception:
        return None


def _is_fresh(dt: datetime | None) -> bool:
    if dt is None:
        return True  # include if we cannot determine age
    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=_MAX_AGE_HOURS)
    return dt >= cutoff


def fetch_rss() -> list[dict]:
    """Fetch and parse all configured RSS feeds. Returns fresh articles only."""
    try:
        import feedparser
    except ImportError:
        logger.error("news_fetcher.rss_import_error", pkg="feedparser")
        return []

    # Some feeds (NSE, BSE) block the default feedparser/Python UA and close
    # the connection immediately. A browser-like UA avoids the drop.
    _HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    }

    articles: list[dict] = []
    for source, url in _RSS_FEEDS.items():
        try:
            feed = feedparser.parse(url, request_headers=_HEADERS)
            for entry in feed.entries:
                published = _parse_dt(entry.get("published_parsed") or entry.get("updated_parsed"))
                if not _is_fresh(published):
                    continue
                title = re.sub(r"<[^>]+>", "", entry.get("title", "")).strip()
                if not title:
                    continue
                articles.append({
                    "title":     title,
                    "url":       entry.get("link"),
                    "published": published or datetime.now(tz=timezone.utc),
                    "source":    source,
                })
        except Exception as exc:
            logger.warning("news_fetcher.rss_failed", source=source, error=str(exc))

    logger.info("news_fetcher.rss_done", count=len(articles))
    return articles


def fetch_google_news(symbols: list[str], max_per_symbol: int = 5) -> list[dict]:
    """Fetch Google News headlines for a list of NSE symbol names.

    ``symbols`` should be plain company names, not ticker codes, e.g.
    ``["Reliance Industries", "Infosys"]``. The caller maps codes → names.
    """
    try:
        from gnews import GNews
    except ImportError:
        logger.error("news_fetcher.gnews_import_error", pkg="gnews")
        return []

    gn = GNews(language="en", country="IN", period="1d", max_results=max_per_symbol)
    articles: list[dict] = []

    for query in symbols:
        try:
            results = gn.get_news(query)
            for item in results:
                published = _parse_dt(item.get("published date"))
                if not _is_fresh(published):
                    continue
                articles.append({
                    "title":     item.get("title", "").strip(),
                    "url":       item.get("url"),
                    "published": published or datetime.now(tz=timezone.utc),
                    "source":    "google_news",
                    "_query":    query,   # temp field: NER mapper uses this as a hint
                })
        except Exception as exc:
            logger.warning("news_fetcher.gnews_failed", query=query, error=str(exc))

    logger.info("news_fetcher.gnews_done", count=len(articles))
    return articles
