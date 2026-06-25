from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from requests_oauthlib import OAuth1Session

from config import config
from logger import logger

_ROOT_DIR = Path(__file__).resolve().parents[2]
_POST_LOG = _ROOT_DIR / "output" / "x_post_log.json"
_POST_ENDPOINT = "https://api.x.com/2/tweets"


def _has_credentials() -> bool:
    return all([
        config.X_CONSUMER_KEY,
        config.X_CONSUMER_SECRET,
        config.X_ACCESS_TOKEN,
        config.X_ACCESS_TOKEN_SECRET,
    ])


def _load_post_log() -> dict:
    if not _POST_LOG.exists():
        return {"posts": []}
    try:
        return json.loads(_POST_LOG.read_text(encoding="utf-8"))
    except Exception:
        logger.warning("X post log is unreadable — resetting log")
        return {"posts": []}


def _save_post_log(data: dict) -> None:
    _POST_LOG.parent.mkdir(parents=True, exist_ok=True)
    _POST_LOG.write_text(json.dumps(data, indent=2), encoding="utf-8")


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
    *,
    title: str,
    video_id: str,
    topic: str = "",
    angle: str = "",
    privacy_status: str = "public",
    pipeline: str = "yt_video",
    hashtags: str = "",
) -> str | None:
    """Post one X update for a public YouTube video, subject to daily cap and dedupe."""
    if not video_id:
        logger.info("X post skipped — missing video_id")
        return None

    if config.X_POST_PUBLIC_ONLY and privacy_status != "public":
        logger.info(f"X post skipped — video privacy is {privacy_status}")
        return None

    if not _has_credentials():
        logger.info("X post skipped — credentials not configured")
        return None

    log_data = _load_post_log()
    posts = log_data.setdefault("posts", [])

    if _already_posted(posts, video_id):
        logger.info(f"X post already created for video_id={video_id} — skipping")
        return None

    if _daily_post_count(posts) >= config.X_DAILY_POST_LIMIT:
        logger.warning(f"X daily post cap reached ({config.X_DAILY_POST_LIMIT}) — skipping")
        return None

    detail = angle.strip() or topic.strip()
    video_url = f"https://www.youtube.com/watch?v={video_id}"
    text = _trim_post_text(title.strip() or topic.strip() or "New video is live", video_url, detail, hashtags)

    client = OAuth1Session(
        client_key=config.X_CONSUMER_KEY,
        client_secret=config.X_CONSUMER_SECRET,
        resource_owner_key=config.X_ACCESS_TOKEN,
        resource_owner_secret=config.X_ACCESS_TOKEN_SECRET,
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
    _save_post_log(log_data)
    logger.info(f"X post created ✓  (tweet_id={tweet_id})")
    return tweet_id
