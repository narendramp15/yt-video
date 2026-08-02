"""
yt_analytics_memory_service.py — yt-video

YouTube Analytics feedback loop: learns from real viewer behaviour to improve
future topic selection and script style.

How it works
------------
After upload:
  long_video.py calls record_video() → stores metadata in memory file.

48+ hours later, run:
  python main.py --fetch-analytics

fetch_pending_analytics() opens an OAuth browser tab, queries the YouTube
Analytics API v2 for each video older than 48h, and persists:
  • views, averageViewPercentage (retention), averageViewDuration
  • likes, subscribersGained, estimatedMinutesWatched

The service then derives two sets of actionable notes from accumulated data:

  get_topic_prompt_injection()   → injected into filter_and_score_topics()
    e.g. "Topics about 'AI' got avg 68% retention. Prefer AI angle."

  get_script_prompt_injection()  → injected into generate_script()
    e.g. "Videos with hooks shorter than 15 words got 12% more retention."

Memory file: output/yt_analytics_memory.json  (~10 KB after 10 runs)
"""

import json
import os
import datetime
from logger import logger
from config import config

# Path for analytics memory (centralized state dir)
_MEMORY_PATH   = config.YT_ANALYTICS_MEMORY_PATH
_FETCH_DELAY_H = 48    # hours to wait after upload before fetching analytics
_GOOD_RETENTION = 45   # % — above this is "strong" retention
_POOR_RETENTION = 30   # % — below this is "weak" retention

# YouTube Analytics API OAuth scope
ANALYTICS_SCOPE = "https://www.googleapis.com/auth/yt-analytics.readonly"


# ---------------------------------------------------------------------------
# Memory I/O
# ---------------------------------------------------------------------------

def _load() -> dict:
    if os.path.exists(_MEMORY_PATH):
        try:
            with open(_MEMORY_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "videos": [],
        "performance_summary": {
            "total_videos_analyzed": 0,
            "avg_retention": 0.0,
            "avg_views": 0.0,
            "top_topic_keywords": [],
            "weak_topic_keywords": [],
        },
        "topic_notes": [],
        "script_notes": [],
        "last_updated": "",
    }


def _save(mem: dict) -> None:
    os.makedirs("output", exist_ok=True)
    with open(_MEMORY_PATH, "w", encoding="utf-8") as f:
        json.dump(mem, f, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Record upload (called immediately after upload_video())
# ---------------------------------------------------------------------------

def record_video(
    video_id: str,
    topic: str,
    angle: str,
    seo_title: str,
    privacy_status: str,
    upload_date: str | None = None,
) -> None:
    """
    Store uploaded video metadata so analytics can be fetched later.

    Parameters
    ----------
    video_id       : YouTube video ID returned by upload_video()
    topic          : trend topic string used for this video
    angle          : angle string from AI topic filter
    seo_title      : the SEO title that was set on YouTube
    privacy_status : 'public' | 'private'
    upload_date    : ISO date string (defaults to today)
    """
    if not video_id:
        logger.warning("yt_analytics: no video_id provided — skipping record_video()")
        return

    mem = _load()
    upload_date = upload_date or datetime.date.today().isoformat()

    # Avoid duplicates
    existing_ids = {v["video_id"] for v in mem.get("videos", [])}
    if video_id in existing_ids:
        logger.info(f"yt_analytics: video {video_id} already recorded")
        return

    entry = {
        "video_id":        video_id,
        "topic":           topic,
        "angle":           angle,
        "seo_title":       seo_title,
        "privacy_status":  privacy_status,
        "upload_date":     upload_date,
        "analytics_ready": False,   # flipped to True after fetch
        "analytics":       None,
    }
    mem.setdefault("videos", []).append(entry)
    mem["last_updated"] = datetime.date.today().isoformat()
    _save(mem)
    logger.info(
        f"yt_analytics: recorded video {video_id} — "
        f"analytics fetchable after {_FETCH_DELAY_H}h "
        f"(run: python main.py --fetch-analytics)"
    )


# ---------------------------------------------------------------------------
# Fetch analytics for pending videos (called by --fetch-analytics flag)
# ---------------------------------------------------------------------------

def fetch_pending_analytics() -> int:
    """
    For every recorded video older than _FETCH_DELAY_H hours that hasn't
    been analyzed yet, fetch its YouTube Analytics data and update memory.

    Opens a single OAuth browser window for the session.

    Returns: count of videos successfully fetched.
    """
    mem = _load()
    pending = [
        v for v in mem.get("videos", [])
        if not v.get("analytics_ready")
        and _hours_since(v.get("upload_date", "")) >= _FETCH_DELAY_H
    ]

    if not pending:
        logger.info("yt_analytics: no videos ready for analytics fetch yet.")
        return 0

    logger.info(f"yt_analytics: fetching analytics for {len(pending)} video(s)...")

    # One OAuth auth for the whole batch
    credentials = _oauth_analytics()
    if credentials is None:
        logger.error("yt_analytics: OAuth failed — cannot fetch analytics")
        return 0

    fetched = 0
    for entry in pending:
        vid   = entry["video_id"]
        udate = entry["upload_date"]
        try:
            data = _fetch_one(credentials, vid, udate)
            entry["analytics"]       = data
            entry["analytics_ready"] = True
            fetched += 1
            logger.info(
                f"  {vid}: views={data.get('views', '?')}, "
                f"retention={data.get('averageViewPercentage', '?')}%, "
                f"likes={data.get('likes', '?')}"
            )
        except Exception as exc:
            logger.warning(f"  {vid}: fetch failed — {exc}")

    mem["last_updated"] = datetime.date.today().isoformat()

    # Re-derive insights from all analyzed data
    analyzed = [v for v in mem["videos"] if v.get("analytics_ready")]
    mem["performance_summary"] = _build_summary(analyzed)
    mem["topic_notes"]   = _derive_topic_notes(analyzed, mem["performance_summary"])
    mem["script_notes"]  = _derive_script_notes(analyzed, mem["performance_summary"])

    _save(mem)
    logger.info(
        f"yt_analytics: {fetched} videos' analytics stored. "
        f"Memory updated with {len(mem['topic_notes'])} topic notes, "
        f"{len(mem['script_notes'])} script notes."
    )
    return fetched


# ---------------------------------------------------------------------------
# OAuth helper
# ---------------------------------------------------------------------------

def _oauth_analytics():
    """Open a browser OAuth flow with analytics read scope. Returns credentials."""
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
        from config import config
        flow = InstalledAppFlow.from_client_secrets_file(
            config.YOUTUBE_CLIENT_SECRET,
            scopes=[ANALYTICS_SCOPE],
        )
        credentials = flow.run_local_server(port=0)
        return credentials
    except Exception as exc:
        logger.error(f"yt_analytics OAuth error: {exc}")
        return None


# ---------------------------------------------------------------------------
# Analytics fetch (YouTube Analytics API v2)
# ---------------------------------------------------------------------------

def _fetch_one(credentials, video_id: str, upload_date: str) -> dict:
    """Fetch key metrics for one video via YouTube Analytics API v2."""
    from googleapiclient.discovery import build

    service = build("youtubeAnalytics", "v2", credentials=credentials)

    start = upload_date
    end   = (
        datetime.date.fromisoformat(upload_date) + datetime.timedelta(days=28)
    ).isoformat()

    result = service.reports().query(
        ids      = "channel==MINE",
        startDate = start,
        endDate   = end,
        metrics  = (
            "views,"
            "estimatedMinutesWatched,"
            "averageViewDuration,"
            "averageViewPercentage,"
            "likes,"
            "subscribersGained"
        ),
        filters  = f"video=={video_id}",
    ).execute()

    rows = result.get("rows", [])
    if not rows:
        return {"views": 0, "averageViewPercentage": 0.0}

    headers = [h["name"] for h in result.get("columnHeaders", [])]
    return dict(zip(headers, rows[0]))


# ---------------------------------------------------------------------------
# Insight derivation
# ---------------------------------------------------------------------------

def _hours_since(date_str: str) -> float:
    try:
        d     = datetime.date.fromisoformat(date_str)
        delta = datetime.date.today() - d
        return delta.total_seconds() / 3600
    except Exception:
        return 0.0


def _build_summary(analyzed: list[dict]) -> dict:
    if not analyzed:
        return {
            "total_videos_analyzed": 0,
            "avg_retention": 0.0,
            "avg_views": 0.0,
            "top_topic_keywords": [],
            "weak_topic_keywords": [],
        }

    retentions = [
        v["analytics"].get("averageViewPercentage", 0.0)
        for v in analyzed if v.get("analytics")
    ]
    views_list = [
        v["analytics"].get("views", 0)
        for v in analyzed if v.get("analytics")
    ]

    avg_ret   = round(sum(retentions) / len(retentions), 1) if retentions else 0.0
    avg_views = round(sum(views_list) / len(views_list), 0) if views_list else 0.0

    # Identify top/weak topics by keyword extraction from topic string
    strong = [
        v for v in analyzed
        if v.get("analytics", {}).get("averageViewPercentage", 0) >= _GOOD_RETENTION
    ]
    weak = [
        v for v in analyzed
        if v.get("analytics", {}).get("averageViewPercentage", 0) < _POOR_RETENTION
        and v.get("analytics", {}).get("views", 0) > 0
    ]

    def _keywords(videos: list[dict]) -> list[str]:
        import re
        words: dict[str, int] = {}
        for v in videos:
            topic = v.get("topic", "") + " " + v.get("angle", "")
            for w in re.findall(r"\b[A-Za-z]{4,}\b", topic.lower()):
                if w not in {"this", "that", "with", "from", "about", "what",
                             "will", "have", "your", "their", "when", "which"}:
                    words[w] = words.get(w, 0) + 1
        return [w for w, _ in sorted(words.items(), key=lambda x: -x[1])[:8]]

    return {
        "total_videos_analyzed": len(analyzed),
        "avg_retention":         avg_ret,
        "avg_views":             avg_views,
        "top_topic_keywords":    _keywords(strong),
        "weak_topic_keywords":   _keywords(weak),
    }


def _derive_topic_notes(analyzed: list[dict], summary: dict) -> list[str]:
    notes = []
    n = summary["total_videos_analyzed"]
    if n == 0:
        return notes

    top_kw  = summary["top_topic_keywords"]
    weak_kw = summary["weak_topic_keywords"]

    if top_kw:
        kw_str = ", ".join(top_kw[:5])
        notes.append(
            f"In {n} past video(s), topics containing these keywords achieved "
            f"≥{_GOOD_RETENTION}% average viewer retention: {kw_str}. "
            "Prefer topics in these areas when scoring."
        )
    if weak_kw:
        kw_str = ", ".join(weak_kw[:5])
        notes.append(
            f"Topics with these keywords consistently underperformed "
            f"(<{_POOR_RETENTION}% retention): {kw_str}. "
            "Penalize or avoid these topic categories."
        )

    # High-publishing-cadence note
    public_vids = [v for v in analyzed if v.get("privacy_status") == "public"]
    if len(public_vids) >= 3:
        pub_rets = [
            v["analytics"].get("averageViewPercentage", 0)
            for v in public_vids if v.get("analytics")
        ]
        if pub_rets:
            best_pub = max(pub_rets)
            notes.append(
                f"Best public video achieved {best_pub:.0f}% retention. "
                "Aim for topics with strong how-to or revelation angles to replicate this."
            )

    return notes


def _derive_script_notes(analyzed: list[dict], summary: dict) -> list[str]:
    notes = []
    n = summary["total_videos_analyzed"]
    if n == 0:
        return notes

    notes.append(
        f"Channel average retention across {n} video(s): {summary['avg_retention']:.0f}%. "
        + (
            "This is strong — keep the fast-paced hook style."
            if summary["avg_retention"] >= _GOOD_RETENTION
            else "Below target. Open with a stronger, faster hook (first 15 seconds are critical)."
        )
    )

    # Engagement ratio (likes per view)
    liked = [
        v for v in analyzed
        if v.get("analytics") and v["analytics"].get("views", 0) > 0
    ]
    if liked:
        ratios = [
            v["analytics"].get("likes", 0) / v["analytics"]["views"]
            for v in liked
        ]
        avg_like_ratio = sum(ratios) / len(ratios)
        if avg_like_ratio < 0.01:
            notes.append(
                "Like-to-view ratio is low. Add a direct, specific CTA near the 70% mark "
                "of the script (not just at the end) — e.g. 'If this surprised you, tap like now.'"
            )

    # Subscribers gained signal
    sub_gains = [
        v["analytics"].get("subscribersGained", 0)
        for v in analyzed if v.get("analytics")
    ]
    if sub_gains and sum(sub_gains) == 0:
        notes.append(
            "No subscribers gained yet. End the video with a specific reason to subscribe "
            "(e.g. 'We cover one trending topic like this every week — subscribe so you never miss it.')."
        )

    return notes


# ---------------------------------------------------------------------------
# Prompt injection (called by workflow + services)
# ---------------------------------------------------------------------------

def get_topic_prompt_injection() -> str:
    """
    Returns a formatted block for filter_and_score_topics() prompt.
    Empty string on first run (no data yet).
    """
    mem = _load()
    notes = mem.get("topic_notes", [])
    summary = mem.get("performance_summary", {})
    n = summary.get("total_videos_analyzed", 0)
    if n == 0 or not notes:
        return ""

    lines = [f"CHANNEL ANALYTICS INSIGHTS (from {n} published video(s) — use to score topics):"]
    for note in notes:
        lines.append(f"  • {note}")
    return "\n".join(lines) + "\n"


def get_script_prompt_injection() -> str:
    """
    Returns a formatted block for generate_script() prompt.
    Empty string on first run (no data yet).
    """
    mem = _load()
    notes = mem.get("script_notes", [])
    summary = mem.get("performance_summary", {})
    n = summary.get("total_videos_analyzed", 0)
    if n == 0 or not notes:
        return ""

    lines = [f"CHANNEL PERFORMANCE NOTES (from {n} published video(s) — follow strictly):"]
    for note in notes:
        lines.append(f"  • {note}")
    return "\n".join(lines) + "\n"
