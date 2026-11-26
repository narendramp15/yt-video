"""YouTube Analytics feedback loop: learns from real viewer behaviour.

After upload, ``record_video`` stores metadata. 48+ hours later ``fetch_pending``
queries the YouTube Analytics API v2 for each video and persists views,
retention, CTR, likes, subscribers and traffic sources. Two sets of actionable
notes are derived from the accumulated data:

  topic_prompt_injection()   -> injected into topic scoring prompts
  script_prompt_injection()  -> injected into script generation prompts
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from yt_video.logging import get_logger
from yt_video.memory._io import read_json, write_json_atomic

if TYPE_CHECKING:  # pragma: no cover
    from google.oauth2.credentials import Credentials

logger = get_logger()

_FETCH_DELAY_H = 48    # hours to wait after upload before fetching analytics
_GOOD_RETENTION = 45   # %: above this is "strong" retention
_POOR_RETENTION = 30   # %: below this is "weak" retention
_GOOD_CTR = 4.0        # %: solid impressions CTR for faceless niches
_POOR_CTR = 2.0        # %: weak packaging signal

# YouTube Analytics API OAuth scope (credentials must be granted this)
ANALYTICS_SCOPE = "https://www.googleapis.com/auth/yt-analytics.readonly"


def _build_analytics_client(credentials: Any) -> Any:
    """Build the YouTube Analytics v2 client (lazy import; monkeypatched in tests)."""
    from googleapiclient.discovery import build

    return build("youtubeAnalytics", "v2", credentials=credentials)


# ---------------------------------------------------------------------------
# Pure functions
# ---------------------------------------------------------------------------

def detect_weak_retention_bands(
    curve: list[dict],
    drop_pts: float = 15.0,
    window: float = 0.10,
) -> list[dict]:
    """Detect segments where retention drops > drop_pts across ~window of elapsed time ratio."""
    if len(curve) < 2:
        return []
    points = sorted(curve, key=lambda p: float(p.get("elapsed_ratio") or 0))
    weak: list[dict] = []
    i = 0
    while i < len(points):
        start_r = float(points[i]["elapsed_ratio"])
        start_ret = float(points[i]["retention"])
        j = i + 1
        while j < len(points) and float(points[j]["elapsed_ratio"]) - start_r < window:
            j += 1
        if j >= len(points):
            break
        end_r = float(points[j]["elapsed_ratio"])
        end_ret = float(points[j]["retention"])
        drop = start_ret - end_ret
        if drop >= drop_pts:
            mid = (start_r + end_r) / 2.0
            weak.append({
                "start_ratio": round(start_r, 3),
                "end_ratio": round(end_r, 3),
                "mid_ratio": round(mid, 3),
                "drop_pts": round(drop, 1),
                "note": _weak_band_script_note(mid, drop),
            })
            i = j
        else:
            i += 1
    return weak


def _weak_band_script_note(mid_ratio: float, drop_pts: float) -> str:
    pct = int(round(mid_ratio * 100))
    if mid_ratio < 0.25:
        where = "early (first quarter)"
        tip = "tighten the cold open and plant an open loop in the first 20 seconds"
    elif mid_ratio < 0.55:
        where = "around mid-video"
        tip = "add a pattern interrupt / open loop before the midpoint"
    else:
        where = "late (final third)"
        tip = "tease a concrete payoff earlier and keep the late CTA short"
    return (
        f"Viewers drop {drop_pts:.0f}pts {where} (~{pct}% mark) — {tip}."
    )


def summarize_retention_curve(curve: list[dict], weak_bands: list[dict] | None = None) -> dict:
    if not curve:
        return {"available": False}
    rets = [float(p.get("retention") or 0) for p in curve]
    return {
        "available": True,
        "points": len(curve),
        "start_retention": round(rets[0], 1) if rets else 0,
        "end_retention": round(rets[-1], 1) if rets else 0,
        "min_retention": round(min(rets), 1) if rets else 0,
        "weak_band_count": len(weak_bands or []),
    }


def _hours_since(date_str: str) -> float:
    try:
        d = datetime.date.fromisoformat(date_str)
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
            "avg_ctr": 0.0,
            "top_topic_keywords": [],
            "weak_topic_keywords": [],
            "top_traffic_sources": [],
        }

    retentions = [
        v["analytics"].get("averageViewPercentage", 0.0)
        for v in analyzed if v.get("analytics")
    ]
    views_list = [
        v["analytics"].get("views", 0)
        for v in analyzed if v.get("analytics")
    ]
    ctrs = [
        float(v["analytics"].get("impressionClickThroughRate") or 0.0)
        for v in analyzed
        if v.get("analytics") and v["analytics"].get("ctr_available")
    ]

    avg_ret = round(sum(retentions) / len(retentions), 1) if retentions else 0.0
    avg_views = round(sum(views_list) / len(views_list), 0) if views_list else 0.0
    avg_ctr = round(sum(ctrs) / len(ctrs), 2) if ctrs else 0.0

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
        words: dict[str, int] = {}
        for v in videos:
            topic = v.get("topic", "") + " " + v.get("angle", "")
            for w in re.findall(r"\b[A-Za-z]{4,}\b", topic.lower()):
                if w not in {"this", "that", "with", "from", "about", "what",
                             "will", "have", "your", "their", "when", "which"}:
                    words[w] = words.get(w, 0) + 1
        return [w for w, _ in sorted(words.items(), key=lambda x: -x[1])[:8]]

    traffic_totals: dict[str, float] = {}
    for v in analyzed:
        for src, val in (v.get("analytics") or {}).get("traffic_sources", {}).items():
            traffic_totals[src] = traffic_totals.get(src, 0.0) + float(val or 0)
    top_traffic = [
        s for s, _ in sorted(traffic_totals.items(), key=lambda x: -x[1])[:5]
    ]

    return {
        "total_videos_analyzed": len(analyzed),
        "avg_retention": avg_ret,
        "avg_views": avg_views,
        "avg_ctr": avg_ctr,
        "top_topic_keywords": _keywords(strong),
        "weak_topic_keywords": _keywords(weak),
        "top_traffic_sources": top_traffic,
    }


def _derive_topic_notes(analyzed: list[dict], summary: dict) -> list[str]:
    notes: list[str] = []
    n = summary["total_videos_analyzed"]
    if n == 0:
        return notes

    top_kw = summary["top_topic_keywords"]
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

    ctr_vids = [
        v for v in analyzed
        if (v.get("analytics") or {}).get("ctr_available")
        and (v.get("analytics") or {}).get("impressionClickThroughRate") is not None
    ]
    if ctr_vids:
        best = max(
            ctr_vids,
            key=lambda v: float(v["analytics"].get("impressionClickThroughRate") or 0),
        )
        worst = min(
            ctr_vids,
            key=lambda v: float(v["analytics"].get("impressionClickThroughRate") or 0),
        )
        best_ctr = float(best["analytics"].get("impressionClickThroughRate") or 0)
        worst_ctr = float(worst["analytics"].get("impressionClickThroughRate") or 0)
        if best_ctr >= _GOOD_CTR:
            notes.append(
                f"Best packaging CTR was {best_ctr:.1f}% "
                f"(title: '{best.get('seo_title') or best.get('title')}'). "
                "Prefer specific numbered/curiosity titles like that winner."
            )
        if worst_ctr and worst_ctr < _POOR_CTR:
            notes.append(
                f"Weak CTR {worst_ctr:.1f}% on '{worst.get('seo_title') or worst.get('title')}'. "
                "Avoid vague or spammy packaging patterns like that loser."
            )

    if summary.get("top_traffic_sources"):
        src = ", ".join(summary["top_traffic_sources"][:3])
        notes.append(
            f"Top traffic sources lately: {src}. "
            "Bias topics toward formats that win those surfaces "
            "(browse=thumb/title punch; search=specific searchable phrases)."
        )

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
    notes: list[str] = []
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

    avg_ctr = float(summary.get("avg_ctr") or 0)
    if avg_ctr > 0:
        notes.append(
            f"Average impressions CTR: {avg_ctr:.1f}%. "
            + (
                "Packaging is working — keep concrete hooks on thumbnails."
                if avg_ctr >= _GOOD_CTR
                else "CTR is soft — tighten titles and 3–5 word thumb text; "
                     "lead with a concrete surprise, not a vague theme."
            )
        )

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

    sub_gains = [
        v["analytics"].get("subscribersGained", 0)
        for v in analyzed if v.get("analytics")
    ]
    if sub_gains and sum(sub_gains) == 0:
        notes.append(
            "No subscribers gained yet. End the video with a specific reason to subscribe "
            "(e.g. 'We cover one trending topic like this every week — subscribe so you never miss it.')."
        )

    # Weak retention band notes from audience retention curves (silent when none yet).
    band_notes: list[str] = []
    for v in analyzed:
        analytics = v.get("analytics") or {}
        if not analytics.get("retention_curve_available"):
            continue
        for band in analytics.get("retention_weak_bands") or []:
            note = (band.get("note") or "").strip()
            if note and note not in band_notes:
                band_notes.append(note)
    notes.extend(band_notes[:4])

    return notes


def _fetch_one(service: Any, video_id: str, upload_date: str) -> dict:
    """Fetch key metrics for one video via YouTube Analytics API v2."""
    start = upload_date
    end = (
        datetime.date.fromisoformat(upload_date) + datetime.timedelta(days=28)
    ).isoformat()

    base_metrics = (
        "views,"
        "estimatedMinutesWatched,"
        "averageViewDuration,"
        "averageViewPercentage,"
        "likes,"
        "subscribersGained"
    )
    ctr_metrics = base_metrics + ",impressions,impressionClickThroughRate"

    data: dict = {"views": 0, "averageViewPercentage": 0.0, "ctr_available": False}

    # Prefer CTR-capable query; fall back if the channel/API rejects those metrics.
    try:
        result = service.reports().query(
            ids="channel==MINE",
            startDate=start,
            endDate=end,
            metrics=ctr_metrics,
            filters=f"video=={video_id}",
        ).execute()
        rows = result.get("rows", [])
        if rows:
            headers = [h["name"] for h in result.get("columnHeaders", [])]
            data = dict(zip(headers, rows[0]))
            data["ctr_available"] = True
        else:
            data = {"views": 0, "averageViewPercentage": 0.0, "ctr_available": True}
    except Exception as exc:
        logger.warning(f"CTR metrics unavailable for {video_id} ({exc}) — legacy metrics only")
        result = service.reports().query(
            ids="channel==MINE",
            startDate=start,
            endDate=end,
            metrics=base_metrics,
            filters=f"video=={video_id}",
        ).execute()
        rows = result.get("rows", [])
        if not rows:
            return {"views": 0, "averageViewPercentage": 0.0, "ctr_available": False}
        headers = [h["name"] for h in result.get("columnHeaders", [])]
        data = dict(zip(headers, rows[0]))
        data["ctr_available"] = False

    # Traffic sources (best-effort)
    try:
        traffic = service.reports().query(
            ids="channel==MINE",
            startDate=start,
            endDate=end,
            metrics="views",
            dimensions="insightTrafficSourceType",
            filters=f"video=={video_id}",
            sort="-views",
            maxResults=8,
        ).execute()
        sources: dict[str, float] = {}
        for row in traffic.get("rows", []) or []:
            if len(row) >= 2:
                sources[str(row[0])] = float(row[1])
        data["traffic_sources"] = sources
    except Exception as exc:
        logger.debug(f"Traffic sources unavailable for {video_id}: {exc}")
        data["traffic_sources"] = {}

    # Audience retention curve (best-effort)
    data["retention_curve_available"] = False
    data["retention_curve"] = []
    data["retention_weak_bands"] = []
    try:
        curve_resp = service.reports().query(
            ids="channel==MINE",
            startDate=start,
            endDate=end,
            metrics="audienceRetention",
            dimensions="elapsedVideoTimeRatio",
            filters=f"video=={video_id}",
        ).execute()
        curve: list[dict] = []
        for row in curve_resp.get("rows", []) or []:
            if len(row) >= 2:
                curve.append({
                    "elapsed_ratio": float(row[0]),
                    "retention": float(row[1]),
                })
        if curve:
            curve.sort(key=lambda p: p["elapsed_ratio"])
            data["retention_curve"] = curve
            data["retention_curve_available"] = True
            data["retention_weak_bands"] = detect_weak_retention_bands(curve)
            data["retention_curve_summary"] = summarize_retention_curve(
                curve, data["retention_weak_bands"]
            )
    except Exception as exc:
        logger.debug(f"Retention curve unavailable for {video_id}: {exc}")

    return data


class AnalyticsMemory:
    """YouTube analytics memory stored at injected paths.

    ``credentials_provider`` returns OAuth credentials (with the analytics
    read scope) on demand; None disables ``fetch_pending``.
    """

    def __init__(
        self,
        memory_path: Path,
        analytics_log_path: Path,
        credentials_provider: Callable[[], "Credentials"] | None,
    ) -> None:
        self.memory_path = Path(memory_path)
        self.analytics_log_path = Path(analytics_log_path)
        self.credentials_provider = credentials_provider

    # -- I/O -----------------------------------------------------------------

    def _load(self) -> dict:
        data = read_json(self.memory_path)
        if isinstance(data, dict):
            return data
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

    def _save(self, mem: dict) -> None:
        write_json_atomic(self.memory_path, mem, ensure_ascii=False)

    # -- recording -----------------------------------------------------------

    def record_video(
        self,
        video_id: str,
        topic: str,
        angle: str,
        seo_title: str,
        privacy_status: str,
        upload_date: str | None = None,
        title: str | None = None,
        thumb_hook: str | None = None,
        packaging_variant_id: str | None = None,
        is_short: bool = False,
        format_id: str | None = None,
    ) -> None:
        """Store uploaded video metadata so analytics can be fetched later."""
        if not video_id:
            logger.warning("yt_analytics: no video_id provided — skipping record_video()")
            return

        mem = self._load()
        upload_date = upload_date or datetime.date.today().isoformat()

        existing_ids = {v["video_id"] for v in mem.get("videos", [])}
        if video_id in existing_ids:
            logger.info(f"yt_analytics: video {video_id} already recorded")
            return

        entry = {
            "video_id": video_id,
            "topic": topic,
            "angle": angle,
            "seo_title": seo_title,
            "title": title or seo_title,
            "thumb_hook": thumb_hook or "",
            "packaging_variant_id": packaging_variant_id or "",
            "format_id": format_id or "",
            "is_short": bool(is_short),
            "privacy_status": privacy_status,
            "upload_date": upload_date,
            "analytics_ready": False,   # flipped to True after fetch
            "analytics": None,
        }
        mem.setdefault("videos", []).append(entry)
        mem["last_updated"] = datetime.date.today().isoformat()
        self._save(mem)
        logger.info(
            f"yt_analytics: recorded video {video_id} — "
            f"analytics fetchable after {_FETCH_DELAY_H}h "
            f"(run: python main.py --fetch-analytics)"
        )

    def log_run(self, video_id: str | None, topic: str, seo_title: str, privacy: str) -> None:
        """Append a minimal publish record to the analytics log."""
        record = {
            "date": datetime.date.today().isoformat(),
            "video_id": video_id,
            "topic": topic,
            "seo_title": seo_title,
            "privacy": privacy,
            "views_48h": None,
            "ctr_48h": None,
        }
        history: list[dict] = []
        existing = read_json(self.analytics_log_path)
        if isinstance(existing, list):
            history = existing
        history.append(record)
        write_json_atomic(self.analytics_log_path, history)
        logger.info(f"Analytics record saved → {self.analytics_log_path}  (video_id={video_id})")

    # -- queries -------------------------------------------------------------

    def recent_public_video_ids(self, limit: int = 3, exclude: str | None = None) -> list[str]:
        """Recent non-short public video ids for Up-next blurbs, newest first."""
        mem = self._load()
        out: list[str] = []
        for v in reversed(mem.get("videos", [])):
            if v.get("is_short"):
                continue
            if (v.get("privacy_status") or "").lower() != "public":
                continue
            vid = v.get("video_id") or ""
            if not vid or vid == exclude:
                continue
            out.append(vid)
            if len(out) >= limit:
                break
        return out

    def recent_video_topics(self, limit: int = 40) -> list[str]:
        """Recent topic/title strings for novelty scoring."""
        mem = self._load()
        out: list[str] = []
        for v in reversed(mem.get("videos", [])):
            for key in ("topic", "seo_title", "title"):
                val = (v.get(key) or "").strip()
                if val:
                    out.append(val)
            if len(out) >= limit:
                break
        return out[:limit]

    def top_topic_keywords(self, limit: int = 8) -> list[str]:
        """Keywords from strong-retention videos (niche-fit fallback)."""
        summary = self._load().get("performance_summary") or {}
        kws = summary.get("top_topic_keywords") or []
        return list(kws)[:limit]

    # -- fetch ---------------------------------------------------------------

    def fetch_pending(self) -> int:
        """Fetch analytics for recorded videos older than 48h; returns count fetched."""
        if self.credentials_provider is None:
            logger.warning("yt_analytics: no credentials provider — cannot fetch analytics")
            return 0

        mem = self._load()
        pending = [
            v for v in mem.get("videos", [])
            if not v.get("analytics_ready")
            and _hours_since(v.get("upload_date", "")) >= _FETCH_DELAY_H
        ]

        if not pending:
            logger.info("yt_analytics: no videos ready for analytics fetch yet.")
            return 0

        logger.info(f"yt_analytics: fetching analytics for {len(pending)} video(s)...")

        try:
            service = _build_analytics_client(self.credentials_provider())
        except Exception as exc:
            logger.error(f"yt_analytics: could not build analytics client: {exc}")
            return 0

        fetched = 0
        for entry in pending:
            vid = entry["video_id"]
            udate = entry["upload_date"]
            try:
                data = _fetch_one(service, vid, udate)
                entry["analytics"] = data
                entry["analytics_ready"] = True
                fetched += 1
                logger.info(
                    f"  {vid}: views={data.get('views', '?')}, "
                    f"retention={data.get('averageViewPercentage', '?')}%, "
                    f"ctr={data.get('impressionClickThroughRate', data.get('ctr', '?'))}, "
                    f"likes={data.get('likes', '?')}"
                )
            except Exception as exc:
                logger.warning(f"  {vid}: fetch failed — {exc}")

        mem["last_updated"] = datetime.date.today().isoformat()

        analyzed = [v for v in mem["videos"] if v.get("analytics_ready")]
        mem["performance_summary"] = _build_summary(analyzed)
        mem["topic_notes"] = _derive_topic_notes(analyzed, mem["performance_summary"])
        mem["script_notes"] = _derive_script_notes(analyzed, mem["performance_summary"])

        self._save(mem)
        logger.info(
            f"yt_analytics: {fetched} videos' analytics stored. "
            f"Memory updated with {len(mem['topic_notes'])} topic notes, "
            f"{len(mem['script_notes'])} script notes."
        )
        return fetched

    # -- prompt injection ----------------------------------------------------

    def topic_prompt_injection(self) -> str:
        """Block for the topic scoring prompt; empty string when no data yet."""
        mem = self._load()
        notes = mem.get("topic_notes", [])
        n = (mem.get("performance_summary") or {}).get("total_videos_analyzed", 0)
        if n == 0 or not notes:
            return ""

        lines = [f"CHANNEL ANALYTICS INSIGHTS (from {n} published video(s) — use to score topics):"]
        for note in notes:
            lines.append(f"  • {note}")
        return "\n".join(lines) + "\n"

    def script_prompt_injection(self) -> str:
        """Block for the script generation prompt; empty string when no data yet."""
        mem = self._load()
        notes = mem.get("script_notes", [])
        n = (mem.get("performance_summary") or {}).get("total_videos_analyzed", 0)
        if n == 0 or not notes:
            return ""

        lines = [f"CHANNEL PERFORMANCE NOTES (from {n} published video(s) — follow strictly):"]
        for note in notes:
            lines.append(f"  • {note}")
        return "\n".join(lines) + "\n"
