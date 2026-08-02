"""
topic_service.py — yt-video  (FREE replacement)

Trend signal sources — all completely free, zero registration required:
  1. NSE API      — live NIFTY 50 market movers           (always runs)
  2. pytrends     — Google Trends India financial queries  (optional, free)
  3. RSS feeds    — ET Markets, Moneycontrol, LiveMint, Business Standard
                    headlines (no account/key needed — stdlib XML parsing)
  4. yfinance     — news headlines for NIFTY index stocks  (already installed)

Replaces: NewsAPI ($49/month) — saves $49/month.
Coverage: Google Trends + financial RSS + yfinance news covers the major
financial topics. Breaking-news detection is supplemented by NIFTY movers.

Install:
  pip install pytrends
  (optional — pipeline works without it; yfinance + RSS always run)
"""

import requests
import xml.etree.ElementTree as ET
from config import config
from logger import logger
from services.cost_service import record_llm


# ---------------------------------------------------------------------------
# NSE market movers (primary, already free)
# ---------------------------------------------------------------------------
def fetch_nifty_movers(top_n: int = 3) -> list[str]:
    """
    Fetch top N gainers from NIFTY 50 via NSE public API.
    Returns list of NSE symbols, e.g. ['RELIANCE', 'HDFCBANK', 'TCS']
    """
    url = "https://www.nseindia.com/api/equity-stockIndices?index=NIFTY%2050"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
        "Referer": "https://www.nseindia.com/",
    }

    logger.info("Fetching NIFTY 50 movers from NSE...")
    try:
        session = requests.Session()
        # NSE requires an initial cookie
        session.get("https://www.nseindia.com/", headers=headers, timeout=10)
        res = session.get(url, headers=headers, timeout=10)
        res.raise_for_status()
        data = res.json()["data"]
    except Exception as exc:
        logger.warning(f"NSE API failed ({exc}) — using fallback symbols")
        return ["RELIANCE", "HDFCBANK", "INFY"]

    sorted_data = sorted(data, key=lambda x: float(x.get("pChange", 0)), reverse=True)
    symbols = [stock["symbol"] for stock in sorted_data[:top_n]]
    logger.info(f"Top movers fetched: {symbols}")
    return symbols


# ---------------------------------------------------------------------------
# Google Trends (free via pytrends)
# ---------------------------------------------------------------------------
def fetch_google_trends_topics(keywords: list[str] | None = None) -> list[str]:
    """
    Fetch trending related queries for financial keywords from Google Trends India.

    Returns list of trending topic strings (may be empty if pytrends unavailable).
    """
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
            kw_list=keywords[:5],   # pytrends limit
            timeframe="now 1-d",
            geo="IN",
        )

        # Rising + top related queries
        related = pytrends.related_queries()
        for kw, data in related.items():
            for category in ("rising", "top"):
                df = data.get(category)
                if df is not None and not df.empty:
                    topics.extend(df["query"].head(3).tolist())

        # Real-time trending searches in India
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
    ("ET Markets",         "https://economictimes.indiatimes.com/markets/rss.cms"),
    ("Moneycontrol",       "https://www.moneycontrol.com/rss/business.xml"),
    ("LiveMint Markets",   "https://www.livemint.com/rss/markets"),
    ("Business Standard",  "https://www.business-standard.com/rss/markets-106.rss"),
]


def fetch_rss_topics(limit_per_feed: int = 5) -> list[str]:
    """
    Fetch latest headlines from Indian financial news RSS feeds.
    Uses only stdlib (xml.etree + requests) — no registration required.
    Returns list of headline strings (may be empty on network error).
    """
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
    """
    Scrape real-time X/Twitter trends from trends24.in (completely free, no login).
    Only reads the most recent hourly trend card to avoid stale data.

    Args:
        country: URL slug, e.g. "india", "united-states", "worldwide"
        limit:   Max number of trends to return (from the most recent card only)
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        logger.debug("beautifulsoup4 not installed — skipping trends24.in scrape")
        return []

    url = f"https://trends24.in/{country}/"
    try:
        res = requests.get(url, headers=_SCRAPE_HEADERS, timeout=12)
        res.raise_for_status()
        res.encoding = "utf-8"   # trends24.in serves UTF-8; force correct decode
        soup = BeautifulSoup(res.text, "html.parser")

        # trends24 renders one <section class="trend-card"> per hour.
        # The first card is the most recent. Each card has an <ol class="trend-card__list">.
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
    """
    Scrape X/Twitter trends from getdaytrends.com (completely free, no login).
    Returns trend names from the main table, stripping metadata suffixes.

    Args:
        country: URL slug, e.g. "india", "united-states"
        limit:   Max number of trends to return
    """
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        logger.debug("beautifulsoup4 not installed — skipping getdaytrends.com scrape")
        return []

    import re as _re
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
            # Second cell: first <a> tag holds the clean trend name
            anchor = tds[1].find("a")
            if anchor:
                name = anchor.get_text(strip=True).lstrip("#").replace("_", " ")
            else:
                # Fallback: strip ": trending position..." suffix
                raw = tds[1].get_text(separator=" ", strip=True)
                name = _re.split(r":\s*trending", raw, flags=_re.IGNORECASE)[0].strip()
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


def fetch_x_trends(country: str = "india") -> list[str]:
    """
    Fetch real-time X/Twitter trending topics for a given country.

    Strategy (in order):
      1. trends24.in   — free scraper, most recent hourly card, up to 25 trends
      2. getdaytrends.com — free scraper, deduplicated fallback / extra topics
      3. Twitter API v1.1 (TWITTER_BEARER_TOKEN) — optional, needs Basic tier ($100/mo)

    All sources are combined and deduplicated. Completely free by default.

    Args:
        country: Country slug used by both scraper URLs, e.g. "india" (default)
    """
    topics: list[str] = []
    seen: set[str] = set()

    def _add(new: list[str]) -> None:
        for t in new:
            key = t.lower().strip()
            if key and key not in seen:
                seen.add(key)
                topics.append(t)

    # 1. trends24.in (primary — most real-time)
    _add(_scrape_trends24(country))

    # 2. getdaytrends.com (secondary — extra coverage / redundancy)
    _add(_scrape_getdaytrends(country))

    # 3. Official Twitter API (optional fallback if Bearer Token is configured)
    token = config.TWITTER_BEARER_TOKEN
    if token and len(topics) < 10:   # only call API if scrapers return too few results
        woeid = config.TWITTER_WOEID
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
# yfinance news (free, already installed)
# ---------------------------------------------------------------------------
def fetch_yfinance_news_topics(symbols: list[str] | None = None) -> list[str]:
    """
    Fetch recent news headlines via yfinance for NIFTY index + top stocks.
    Requires no API key — yfinance is already a core dependency.
    Returns list of headline strings (may be empty on error).
    """
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
def fetch_trending_topics() -> list[str]:
    """
    Aggregate trend signals from all sources.
    Returns deduplicated list of trending topic strings.

    Sources (in priority order):
      1. X/Twitter trending topics — real-time, high-signal (requires Bearer Token)
      2. Google Trends India        — free via pytrends
      3. Financial RSS feeds        — ET Markets, Moneycontrol, LiveMint, BS
      4. yfinance news              — NIFTY index + top stock headlines
    """
    topics: list[str] = []
    seen: set[str]    = set()

    all_raw = (
        fetch_x_trends()               # X/Twitter — highest signal, put first
        + fetch_google_trends_topics()
        + fetch_rss_topics()
        + fetch_yfinance_news_topics()
    )

    for topic in all_raw:
        key = topic.lower().strip()
        if key and key not in seen:
            seen.add(key)
            topics.append(topic)

    logger.info(f"Combined trend signals: {len(topics)} unique topics")
    return topics[:30]


# ---------------------------------------------------------------------------
# AI topic filter & scorer
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> set[str]:
    import re
    stop = {
        "this", "that", "with", "from", "about", "what", "will", "have",
        "your", "their", "when", "which", "into", "over", "after", "before",
        "today", "week", "india", "indian", "video", "news",
        "an", "as", "at", "be", "by", "do", "if", "in", "is", "it", "no",
        "of", "on", "or", "so", "to", "up", "we",
    }
    # Keep 2+ char tokens so niche keywords like "ai" / "ipl" survive.
    return {
        w for w in re.findall(r"[a-z0-9]{2,}", (text or "").lower())
        if w not in stop
    }


def _recent_topic_titles() -> list[str]:
    """Titles/topics already posted — used for novelty penalty."""
    titles: list[str] = []
    try:
        from services.yt_analytics_memory_service import recent_video_topics
        titles.extend(recent_video_topics(limit=40))
    except Exception:
        pass
    return titles


def _niche_keywords() -> list[str]:
    from config import config
    niche = list(config.CHANNEL_PRIMARY_NICHE or [])
    if niche:
        return niche
    try:
        from services.yt_analytics_memory_service import get_top_topic_keywords
        return get_top_topic_keywords(limit=8)
    except Exception:
        return []


def _configured_niche_keywords() -> list[str]:
    """Explicit CHANNEL_PRIMARY_NICHE only (not analytics fallback)."""
    from config import config
    return list(config.CHANNEL_PRIMARY_NICHE or [])


def niche_lock_prompt_note() -> str:
    """Inject into topic + script prompts when niche lock is active."""
    from config import config
    niche = _configured_niche_keywords()
    if not niche:
        return ""
    days = getattr(config, "NICHE_LOCK_DAYS", 30)
    kw = ", ".join(niche)
    return (
        f"NICHE LOCK ({days}-day primary niche): stay inside [{kw}]. "
        f"Frame every topic/angle through this niche. Drop off-niche digressions.\n"
    )


def apply_niche_hard_filter(ranked: list[dict]) -> list[dict]:
    """
    Phase C hard niche lock.

    When CHANNEL_PRIMARY_NICHE is set and NICHE_LOCK_ENABLED:
      drop topics with zero token overlap against niche keywords.
    If every topic is filtered out: keep the top LLM-scored item and force
    its angle toward the niche.
    When niche keywords are empty: warn and skip hard filter.
    """
    from config import config

    niche = _configured_niche_keywords()
    if not niche:
        logger.warning(
            "CHANNEL_PRIMARY_NICHE is empty — niche hard lock skipped "
            "(soft niche re-rank still uses analytics keywords when available)"
        )
        return ranked

    if not getattr(config, "NICHE_LOCK_ENABLED", True):
        logger.info("NICHE_LOCK_ENABLED=false — skipping hard niche filter")
        return ranked

    niche_set = set(niche)
    kept: list[dict] = []
    for item in ranked:
        hay = f"{item.get('title', '')} {item.get('angle', '')}"
        tokens = _tokenize(hay)
        if tokens & niche_set:
            row = dict(item)
            row["niche_match"] = True
            kept.append(row)

    if kept:
        logger.info(
            f"Niche hard filter: kept {len(kept)}/{len(ranked)} topics "
            f"(niche={','.join(niche)})"
        )
        return kept

    # Fallback: keep best original score, rewrite angle toward niche
    if not ranked:
        return ranked
    best = dict(max(ranked, key=lambda x: float(x.get("score") or 0)))
    niche_str = ", ".join(niche)
    forced = (
        f"Frame through {niche_str}: "
        f"{best.get('angle') or best.get('title') or 'explain why it matters'}"
    )
    best["angle"] = forced
    best["niche_match"] = False
    best["niche_forced"] = True
    logger.warning(
        f"Niche hard filter removed all topics — keeping '{best.get('title')}' "
        f"with forced niche angle"
    )
    return [best]


def rerank_topics(
    ranked: list[dict],
    raw_signals: list[str] | None = None,
    recent_topics: list[str] | None = None,
    top_n: int = 3,
) -> list[dict]:
    """
    Blend LLM score with deterministic novelty / velocity / niche adjustments.
    """
    if not ranked:
        return []

    signals = raw_signals or []
    recent = recent_topics if recent_topics is not None else _recent_topic_titles()
    recent_tokens = [_tokenize(t) for t in recent if t]
    niche = set(_niche_keywords())

    adjusted: list[dict] = []
    for item in ranked:
        title = str(item.get("title") or "")
        tokens = _tokenize(title + " " + str(item.get("angle") or ""))
        base = float(item.get("score") or 0.0)
        delta = 0.0

        # Novelty: penalize overlap with recently posted topics
        if recent_tokens and tokens:
            best_overlap = max(
                (len(tokens & rt) / max(len(tokens), 1) for rt in recent_tokens),
                default=0.0,
            )
            if best_overlap >= 0.5:
                delta -= 2.5
            elif best_overlap >= 0.3:
                delta -= 1.25

        # Velocity: bonus if topic tokens appear across multiple raw signals
        if signals and tokens:
            hits = sum(
                1 for s in signals
                if tokens & _tokenize(s)
            )
            if hits >= 3:
                delta += 1.5
            elif hits >= 2:
                delta += 0.75

        # Niche fit
        if niche and tokens & niche:
            delta += 1.25

        row = dict(item)
        row["llm_score"] = base
        row["score"] = round(base + delta, 2)
        row["rerank_delta"] = round(delta, 2)
        adjusted.append(row)

    adjusted.sort(key=lambda x: float(x.get("score") or 0), reverse=True)
    out = adjusted[:top_n]
    if out:
        logger.info(
            f"Topic re-rank: top='{out[0].get('title')}' "
            f"score={out[0].get('score')} (delta={out[0].get('rerank_delta')})"
        )
    return out


def filter_and_score_topics(
    raw_topics: list[str],
    top_n: int = 3,
) -> list[dict]:
    """
    Use AI to filter sensitive topics and score the rest by virality potential,
    then apply deterministic novelty/velocity/niche re-ranking.

    Returns a list of up to top_n dicts:
      [{"title": "...", "angle": "...", "audience": "...", "score": 9.2}, ...]

    Falls back to simple ranking (first top_n topics) if the AI call fails.
    """
    if not raw_topics:
        return []

    ranked: list[dict] = []
    try:
        from openai import OpenAI
        from config import config

        model = config.SCRIPT_MODEL
        if model.startswith("deepseek"):
            client = OpenAI(
                base_url="https://api.deepseek.com",
                api_key=config.DEEPSEEK_API_KEY or config.OPENAI_API_KEY,
            )
        else:
            client = OpenAI(api_key=config.OPENAI_API_KEY)

        # Ask LLM for a wider pool so re-rank has room to reshuffle
        pool_n = min(max(top_n * 3, top_n), 12)
        numbered = "\n".join(f"{i+1}. {t}" for i, t in enumerate(raw_topics[:30]))

        # Inject channel analytics insights so AI scores toward proven performers
        analytics_block = ""
        try:
            from services.yt_analytics_memory_service import get_topic_prompt_injection
            analytics_block = get_topic_prompt_injection()
        except Exception:
            pass
        niche_block = niche_lock_prompt_note()

        prompt = f"""You are a YouTube trend analyst for an Indian audience (18–35, English-speaking).

Given these {min(len(raw_topics), 30)} trending topics, do the following:
1. FILTER OUT anything political, controversial, religious, or potentially sensitive.
2. SCORE the remaining topics 1–10 by YouTube virality potential for India.
3. Return the top {pool_n} topics as a JSON array.
{analytics_block}{niche_block}
Topics:
{numbered}

Return ONLY this JSON (no explanation, no markdown):
[
  {{"title": "...", "angle": "one-sentence video angle", "audience": "target viewer type", "score": 9.2}},
  ...
]"""

        res = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
        )
        if res.usage:
            record_llm("yt_video", "filter_topics", model,
                       res.usage.prompt_tokens, res.usage.completion_tokens)
        raw = (res.choices[0].message.content or "").strip()

        import json
        import re
        # Extract JSON array even if wrapped in ```
        match = re.search(r"\[.*\]", raw, re.DOTALL)
        if match:
            ranked = json.loads(match.group())
            if ranked:
                logger.info(
                    f"AI topic filter: {len(ranked)} topics ranked — "
                    f"top: '{ranked[0].get('title', '')}'"
                )
            else:
                logger.warning("AI topic filter returned an empty list — using raw fallback")
        else:
            logger.warning("AI topic filter returned no valid JSON — using raw fallback")

    except Exception as exc:
        logger.warning(f"AI topic filter failed ({exc}) — using raw topic list as fallback")

    if not ranked:
        ranked = [{"title": t, "angle": "", "audience": "general", "score": 0.0}
                  for t in raw_topics[: max(top_n * 3, top_n)]]

    # Phase C: hard niche lock before soft re-rank
    ranked = apply_niche_hard_filter(ranked)

    return rerank_topics(ranked, raw_signals=raw_topics, top_n=top_n)


# ---------------------------------------------------------------------------
# Content angle (day-of-week scheduling)
# ---------------------------------------------------------------------------
def get_content_angle(topic: str) -> str:
    """
    Return a day-of-week-aware framing angle for the given topic.

    Monday  → Weekly Preview   (forward-looking, "what to watch this week")
    Friday  → Weekend Deep-Dive (reflective, top picks of the week)
    Other   → Daily Trending   (in-the-moment, what's happening today)
    """
    import datetime
    weekday = datetime.date.today().weekday()   # 0=Monday … 6=Sunday
    if weekday == 0:
        return "Weekly Preview"
    if weekday == 4:
        return "Weekend Deep-Dive"
    return "Daily Trending"
