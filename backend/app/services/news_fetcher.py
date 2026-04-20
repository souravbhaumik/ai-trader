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
    # Additional sources for broader coverage
    "hindu_bl":       "https://www.thehindubusinessline.com/markets/?service=rss",
    "financial_exp":  "https://www.financialexpress.com/market/feed/",
    "ndtv_profit":    "https://feeds.feedburner.com/ndtvprofit-latest",
    "zee_biz":        "https://www.zeebiz.com/rss",
}

# Max hours in the past to consider an article "fresh"
_MAX_AGE_HOURS = 24


def _parse_dt(raw: Any) -> datetime | None:
    """Convert a feedparser time_struct or ISO string to a UTC datetime."""
    if raw is None:
        return None
    try:
        import calendar as _cal
        if hasattr(raw, "tm_year"):                  # feedparser time_struct
            ts = _cal.timegm(raw)                    # interpret as UTC, not local
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
                raw_summary = entry.get("summary") or entry.get("description") or ""
                summary = re.sub(r"<[^>]+>", "", raw_summary).strip()
                summary = summary[:800] if summary else None
                articles.append({
                    "title":     title,
                    "summary":   summary,
                    "url":       entry.get("link"),
                    "published": published or datetime.now(tz=timezone.utc),
                    "source":    source,
                })
        except Exception as exc:
            logger.warning("news_fetcher.rss_failed", source=source, err=str(exc))

    logger.info("news_fetcher.rss_done", count=len(articles))
    return articles


def fetch_yahoo_finance_news(tickers: list[str], max_per_ticker: int = 5) -> list[dict]:
    """Fetch Yahoo Finance news for a list of NSE tickers (with .NS suffix).

    Unlike Google News these articles are already symbol-tagged so no NER
    is needed — the caller should attach the symbol directly.
    """
    try:
        import yfinance as yf
    except ImportError:
        logger.error("news_fetcher.yfinance_import_error", pkg="yfinance")
        return []

    articles: list[dict] = []
    for ticker_code in tickers:
        try:
            ticker = yf.Ticker(ticker_code)
            news_items = ticker.news or []
            for item in news_items[:max_per_ticker]:
                content = item.get("content", {})
                title = (content.get("title") or item.get("title") or "").strip()
                if not title:
                    continue
                # providerPublishTime is a unix timestamp
                pub_ts = (
                    content.get("pubDate")
                    or item.get("providerPublishTime")
                )
                if isinstance(pub_ts, (int, float)):
                    published = datetime.fromtimestamp(pub_ts, tz=timezone.utc)
                else:
                    published = _parse_dt(pub_ts)
                if not _is_fresh(published):
                    continue
                summary_raw = (
                    content.get("summary")
                    or content.get("description")
                    or item.get("summary", "")
                    or ""
                )
                summary = re.sub(r"<[^>]+>", "", summary_raw).strip()[:800] or None
                link = (
                    content.get("canonicalUrl", {}).get("url")
                    or item.get("link")
                )
                articles.append({
                    "title":     title,
                    "summary":   summary,
                    "url":       link,
                    "published": published or datetime.now(tz=timezone.utc),
                    "source":    "yahoo_finance",
                    # Pass the raw ticker so NER mapper can use it as a direct hint
                    "_ticker":   ticker_code,
                })
        except Exception as exc:
            logger.warning("news_fetcher.yfinance_failed", ticker=ticker_code, err=str(exc))

    logger.info("news_fetcher.yfinance_done", count=len(articles))
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

    import time as _t
    for i, query in enumerate(symbols):
        if i > 0 and i % 5 == 0:
            _t.sleep(1.0)  # Rate-limit: 5 queries per second max
        try:
            results = gn.get_news(query)
            for item in results:
                published = _parse_dt(item.get("published date"))
                if not _is_fresh(published):
                    continue
                articles.append({
                    "title":     item.get("title", "").strip(),
                    "summary":   (item.get("description") or "")[:800] or None,
                    "url":       item.get("url"),
                    "published": published or datetime.now(tz=timezone.utc),
                    "source":    "google_news",
                    "_query":    query,   # temp field: NER mapper uses this as a hint
                })
        except Exception as exc:
            logger.warning("news_fetcher.gnews_failed", query=query, err=str(exc))

    logger.info("news_fetcher.gnews_done", count=len(articles))
    return articles
