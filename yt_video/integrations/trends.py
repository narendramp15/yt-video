"""Trend signal sources: X trends (scrape/API), Google Trends, RSS, yfinance news.

Ported from services/topic_service.py (minus NSE movers and the AI scorer).
Every source is isolated: a failure logs a warning and contributes nothing.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Callable

import requests

from yt_video.logging import get_logger
from yt_video.settings import Settings

logger = get_logger()


# ---------------------------------------------------------------------------
# Google Trends (free via pytrends)
# ---------------------------------------------------------------------------
def fetch_google_trends_topics(keywords: list[str] | None = None) -> list[str]:
    """Trending related queries for financial keywords from Google Trends India."""
    try:
        from pytrends.request import TrendReq
    except ImportError:
        logger.debug("pytrends not installed — skipping Google Trends signals")
        return []

    if keywords is None:
        keywords = ["NIFTY 50", "stock market India", "NSE India"]

    topics: list[str] = []
    try:
        logger.info("Fetching Google Trends signals (India, last 24h)...")
        pytrends = TrendReq(hl="en-IN", tz=330, timeout=(10, 25))  # IST = UTC+5:30
        pytrends.build_payload(
            kw_list=keywords[:5],  # pytrends limit
            timeframe="now 1-d",
            geo="IN",
        )

        related = pytrends.related_queries()
        for _kw, data in related.items():
            for category in ("rising", "top"):
                df = data.get(category)
                if df is not None and not df.empty:
                    topics.extend(df["query"].head(3).tolist())

        try:
            trending_df = pytrends.trending_searches(pn="india")
            topics.extend(trending_df[0].head(5).tolist())
        except Exception:
            pass

        logger.info(f"Google Trends: {len(topics)} signals fetched")
    except Exception as exc:
        logger.warning(f"Google Trends fetch failed: {exc}")

    return topics


# ---------------------------------------------------------------------------
# Financial RSS feeds (free, no registration, stdlib XML)
# ---------------------------------------------------------------------------
_RSS_FEEDS = [
    ("ET Markets", "https://economictimes.indiatimes.com/markets/rss.cms"),
    ("Moneycontrol", "https://www.moneycontrol.com/rss/business.xml"),
    ("LiveMint Markets", "https://www.livemint.com/rss/markets"),
    ("Business Standard", "https://www.business-standard.com/rss/markets-106.rss"),
]


def fetch_rss_topics(limit_per_feed: int = 5) -> list[str]:
    """Latest headlines from Indian financial news RSS feeds (may be empty)."""
    topics: list[str] = []
    headers = {"User-Agent": "Mozilla/5.0 (compatible; yt-video-bot/1.0)"}

    for name, url in _RSS_FEEDS:
        try:
            res = requests.get(url, headers=headers, timeout=8)
            res.raise_for_status()
            root = ET.fromstring(res.content)
            titles = [
                item.findtext("title", "").strip()
                for item in root.iter("item")
                if item.findtext("title", "").strip()
            ]
            topics.extend(titles[:limit_per_feed])
            logger.debug(f"RSS {name}: {len(titles[:limit_per_feed])} headlines")
        except Exception as exc:
            logger.debug(f"RSS feed '{name}' failed: {exc}")

    logger.info(f"RSS feeds: {len(topics)} headlines fetched")
    return topics


# ---------------------------------------------------------------------------
# X / Twitter trending topics — free web scrapers (no API key needed)
# ---------------------------------------------------------------------------
_SCRAPE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def _scrape_trends24(country: str = "india", limit: int = 25) -> list[str]:
    """Scrape the most recent hourly trend card from trends24.in."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        logger.debug("beautifulsoup4 not installed — skipping trends24.in scrape")
        return []

    url = f"https://trends24.in/{country}/"
    try:
        res = requests.get(url, headers=_SCRAPE_HEADERS, timeout=12)
        res.raise_for_status()
        res.encoding = "utf-8"  # trends24.in serves UTF-8; force correct decode
        soup = BeautifulSoup(res.text, "html.parser")

        first_list = soup.select_one("ol.trend-card__list")
        if not first_list:
            logger.debug("trends24.in: could not find trend-card__list — structure may have changed")
            return []

        topics = [
            a.get_text(strip=True).lstrip("#").replace("_", " ")
            for a in first_list.select("li a")
            if a.get_text(strip=True)
        ][:limit]

        logger.info(f"trends24.in: {len(topics)} trending topics scraped")
        return topics

    except Exception as exc:
        logger.warning(f"trends24.in scrape failed: {exc}")
        return []


def _scrape_getdaytrends(country: str = "india", limit: int = 25) -> list[str]:
    """Scrape trend names from getdaytrends.com, stripping metadata suffixes."""
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        logger.debug("beautifulsoup4 not installed — skipping getdaytrends.com scrape")
        return []

    url = f"https://getdaytrends.com/{country}/"
    try:
        res = requests.get(url, headers=_SCRAPE_HEADERS, timeout=12)
        res.raise_for_status()
        soup = BeautifulSoup(res.text, "html.parser")

        topics: list[str] = []
        for row in soup.select("table tr"):
            tds = row.find_all("td")
            if len(tds) < 2:
                continue
            anchor = tds[1].find("a")
            if anchor:
                name = anchor.get_text(strip=True).lstrip("#").replace("_", " ")
            else:
                raw = tds[1].get_text(separator=" ", strip=True)
                name = re.split(r":\s*trending", raw, flags=re.IGNORECASE)[0].strip()
                name = name.lstrip("#").replace("_", " ")
            if name and not name.isdigit():
                topics.append(name)
            if len(topics) >= limit:
                break

        logger.info(f"getdaytrends.com: {len(topics)} trending topics scraped")
        return topics

    except Exception as exc:
        logger.warning(f"getdaytrends.com scrape failed: {exc}")
        return []


def fetch_x_trends(settings: Settings, country: str = "india") -> list[str]:
    """Real-time X trends: trends24.in, getdaytrends.com, then the optional API.

    All sources are combined and deduplicated; the API is only called when a
    bearer token is configured and the scrapers returned fewer than 10 topics.
    """
    topics: list[str] = []
    seen: set[str] = set()

    def _add(new: list[str]) -> None:
        for t in new:
            key = t.lower().strip()
            if key and key not in seen:
                seen.add(key)
                topics.append(t)

    _add(_scrape_trends24(country))
    _add(_scrape_getdaytrends(country))

    token = settings.twitter_bearer_token
    if token and len(topics) < 10:
        woeid = settings.twitter_woeid
        api_url = f"https://api.twitter.com/1.1/trends/place.json?id={woeid}"
        try:
            res = requests.get(api_url, headers={"Authorization": f"Bearer {token}"}, timeout=10)
            if res.status_code == 200:
                data = res.json()
                raw = data[0].get("trends", []) if isinstance(data, list) and data else []
                sorted_raw = sorted(raw, key=lambda t: t.get("tweet_volume") or 0, reverse=True)
                _add([t["name"].lstrip("#").replace("_", " ") for t in sorted_raw if t.get("name")])
                logger.info(f"X API fallback: added trends from WOEID={woeid}")
            else:
                logger.debug(f"X API returned {res.status_code} — skipping")
        except Exception as exc:
            logger.debug(f"X API fallback failed: {exc}")

    logger.info(f"fetch_x_trends: {len(topics)} total X trends (country={country})")
    return topics[:30]


# ---------------------------------------------------------------------------
# yfinance news (free)
# ---------------------------------------------------------------------------
def fetch_yfinance_news_topics(symbols: list[str] | None = None) -> list[str]:
    """Recent news headlines via yfinance for NIFTY index + top stocks."""
    try:
        import yfinance as yf
    except ImportError:
        logger.debug("yfinance not installed — skipping yfinance news signals")
        return []

    if symbols is None:
        symbols = ["^NSEI", "RELIANCE.NS", "HDFCBANK.NS", "INFY.NS", "TCS.NS"]

    topics: list[str] = []
    for sym in symbols:
        try:
            news = yf.Ticker(sym).news or []
            for item in news[:3]:
                title = (item.get("content", {}).get("title") or item.get("title") or "").strip()
                if title:
                    topics.append(title)
        except Exception as exc:
            logger.debug(f"yfinance news for {sym} failed: {exc}")

    logger.info(f"yfinance news: {len(topics)} headlines fetched")
    return topics


# ---------------------------------------------------------------------------
# Combined trend signal aggregator
# ---------------------------------------------------------------------------
def _safe(name: str, fn: Callable[[], list[str]]) -> list[str]:
    try:
        return list(fn() or [])
    except Exception as exc:
        logger.warning(f"Trend source '{name}' failed: {exc}")
        return []


def fetch_trending_topics(settings: Settings) -> list[str]:
    """Aggregate trend signals (X, Google Trends, RSS, yfinance), deduplicated.

    Order is priority order; dedupe is case-insensitive on the stripped text and
    keeps first-seen order. Capped at 30.
    """
    all_raw = (
        _safe("x", lambda: fetch_x_trends(settings))
        + _safe("google_trends", fetch_google_trends_topics)
        + _safe("rss", fetch_rss_topics)
        + _safe("yfinance", fetch_yfinance_news_topics)
    )

    topics: list[str] = []
    seen: set[str] = set()
    for topic in all_raw:
        key = topic.lower().strip()
        if key and key not in seen:
            seen.add(key)
            topics.append(topic)

    logger.info(f"Combined trend signals: {len(topics)} unique topics")
    return topics[:30]
