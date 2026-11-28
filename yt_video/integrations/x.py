"""Post a short X (Twitter) update after a YouTube upload.

Ported from services/x_service.py; the post log lives at a caller-supplied path.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from requests_oauthlib import OAuth1Session

from yt_video.logging import get_logger
from yt_video.memory._io import read_json, write_json_atomic
from yt_video.settings import Settings

logger = get_logger()

_POST_ENDPOINT = "https://api.x.com/2/tweets"


def _has_credentials(settings: Settings) -> bool:
    return all([
        settings.x_consumer_key,
        settings.x_consumer_secret,
        settings.x_access_token,
        settings.x_access_token_secret,
    ])


def _already_posted(posts: list[dict], video_id: str) -> bool:
    return any(post.get("video_id") == video_id for post in posts)


def _daily_post_count(posts: list[dict]) -> int:
    today = str(date.today())
    return sum(1 for post in posts if post.get("date") == today)


def _trim_post_text(title: str, video_url: str, detail: str = "", hashtags: str = "") -> str:
    hashtag_str = f"\n{hashtags}" if hashtags else ""
    suffix = f"\n\n{video_url}{hashtag_str}"
    if detail:
        candidate = f"{title}\n\n{detail}{suffix}"
        if len(candidate) <= 280:
            return candidate

    candidate = f"{title}{suffix}"
    if len(candidate) <= 280:
        return candidate

    max_title_len = 280 - len(suffix) - 1
    short_title = title[:max_title_len].rstrip()
    return f"{short_title}…{suffix}"


def post_video_update(
    settings: Settings,
    log_path: Path,
    title: str,
    video_id: str,
    topic: str = "",
    angle: str = "",
    privacy_status: str = "public",
    pipeline: str = "yt_video",
    hashtags: str = "",
) -> str | None:
    """Post one X update for a public video, subject to daily cap and dedupe.

    Returns the tweet id, or None when skipped or failed.
    """
    if not video_id:
        logger.info("X post skipped — missing video_id")
        return None

    if settings.x_post_public_only and privacy_status != "public":
        logger.info(f"X post skipped — video privacy is {privacy_status}")
        return None

    if not _has_credentials(settings):
        logger.info("X post skipped — credentials not configured")
        return None

    log_data = read_json(log_path, dict) or {"posts": []}
    posts = log_data.setdefault("posts", [])

    if _already_posted(posts, video_id):
        logger.info(f"X post already created for video_id={video_id} — skipping")
        return None

    if _daily_post_count(posts) >= settings.x_daily_post_limit:
        logger.warning(f"X daily post cap reached ({settings.x_daily_post_limit}) — skipping")
        return None

    detail = angle.strip() or topic.strip()
    video_url = f"https://www.youtube.com/watch?v={video_id}"
    text = _trim_post_text(
        title.strip() or topic.strip() or "New video is live", video_url, detail, hashtags
    )

    client = OAuth1Session(
        client_key=settings.x_consumer_key,
        client_secret=settings.x_consumer_secret,
        resource_owner_key=settings.x_access_token,
        resource_owner_secret=settings.x_access_token_secret,
    )

    response = client.post(_POST_ENDPOINT, json={"text": text}, timeout=30)
    if response.status_code >= 400:
        try:
            err = response.json()
        except Exception:
            err = {}
        if err.get("title") == "CreditsDepleted" or response.status_code == 402:
            logger.warning(
                "X post skipped — account credits depleted. "
                "Top up at https://developer.x.com/en/portal/dashboard"
            )
        else:
            logger.warning(f"X post failed ({response.status_code}): {response.text[:300]}")
        return None

    payload = response.json()
    tweet_id = payload.get("data", {}).get("id")
    if not tweet_id:
        logger.warning("X post response missing tweet id")
        return None

    posts.append({
        "date": str(date.today()),
        "tweet_id": tweet_id,
        "video_id": video_id,
        "title": title,
        "privacy": privacy_status,
        "pipeline": pipeline,
    })
    write_json_atomic(log_path, log_data)
    logger.info(f"X post created (tweet_id={tweet_id})")
    return tweet_id
