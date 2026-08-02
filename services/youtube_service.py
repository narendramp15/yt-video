"""
youtube_service.py — yt-video

Upload finished video to YouTube via Google OAuth 2.0.

Identical to v1 pipeline; imports updated for standalone yt-video package.

TODO (enhancement): add token caching to avoid re-auth on every run.
  Implement: google.oauth2.credentials + token.json file.
"""

from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from config import config
from logger import logger

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]


# Keep defaults niche-neutral. Never tag "AI generated / AI voice" — that
# brands uploads as low-trust filler and tanks CTR in browse/search.
_DEFAULT_TAGS = [
    "trending topics", "India news", "explainer",
    "educational", "YouTube India",
    config.CHANNEL_NAME or "",
]

_AI_DISCLOSURE = (
    "\n\n---\n"
    "This video uses AI-assisted script writing and AI-generated voice narration. "
    "For educational purposes only."
)


_youtube_client = None
_youtube_client_token = None


def _get_cached_credentials(token_path: str):
    from pathlib import Path

    token_file = Path(token_path)
    creds = None
    if token_file.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
        except Exception:
            creds = None

    if creds and creds.valid:
        return creds

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            token_file.parent.mkdir(parents=True, exist_ok=True)
            token_file.write_text(creds.to_json())
            return creds
        except Exception:
            pass

    logger.info("Starting YouTube OAuth flow...")
    flow = InstalledAppFlow.from_client_secrets_file(config.YOUTUBE_CLIENT_SECRET, SCOPES)
    creds = flow.run_local_server(port=0)
    logger.info("OAuth authentication successful")
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(creds.to_json())
    return creds


def _build_youtube_client():
    token_path = str(config.STATE_DIR / "youtube_token.json")
    global _youtube_client, _youtube_client_token
    creds = _get_cached_credentials(token_path)
    token = getattr(creds, "token", None)
    if _youtube_client is None or _youtube_client_token != token:
        _youtube_client = build("youtube", "v3", credentials=creds)
        _youtube_client_token = token
    return _youtube_client


def _sanitize_title(title: str) -> str:
    replacements = {"▲": "+", "▼": "-", "△": "+", "▽": "-", "<": "(", ">": ")"}
    for char, sub in replacements.items():
        title = title.replace(char, sub)
    title = title.strip()[:100]
    return title or "Untitled"


def upload_video(
    title: str,
    description: str,
    privacy_status: str = "private",
    video_path: str = "output/final_video.mp4",
    thumbnail_path: str | None = "output/images/thumbnail.jpg",
    tags: list[str] | None = None,
    category_id: str | None = None,
) -> str:
    """
    Upload video to YouTube.

    Parameters
    ----------
    title          : video title
    description    : video description
    privacy_status : 'public' | 'private' | 'unlisted'
    video_path     : path to the mp4 file
    thumbnail_path : optional path to thumbnail image (JPG/PNG)
    tags           : optional list of tags (falls back to _DEFAULT_TAGS)
    category_id    : YouTube categoryId (falls back to config.DEFAULT_CATEGORY)

    Returns: YouTube video ID string.
    """
    import os

    title = _sanitize_title(title)
    logger.info("Starting YouTube OAuth flow...")
    youtube = _build_youtube_client()
    logger.info("OAuth authentication successful")

    # Drop any AI-sludge tags if a caller still passes them
    _BLOCKED_TAGS = {"ai generated", "ai voice", "ai narration", "ai-generated"}
    final_tags = (tags or []) + _DEFAULT_TAGS
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in final_tags:
        tag = (raw or "").strip()
        if not tag:
            continue
        key = tag.lower()
        if key in _BLOCKED_TAGS or key in seen:
            continue
        seen.add(key)
        cleaned.append(tag)
    final_tags = cleaned[:30]
    description = description + _AI_DISCLOSURE
    resolved_category = (category_id or config.DEFAULT_CATEGORY or "22").strip()

    body = {
        "snippet": {
            "title":       title,
            "description": description,
            "categoryId":  resolved_category,
            "tags":        final_tags,
        },
        "status": {
            "privacyStatus":           privacy_status,
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(video_path, chunksize=-1, resumable=True)

    logger.info(f"Uploading video: '{title}' (privacy={privacy_status})")
    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )

    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            pct = int(status.progress() * 100)
            logger.info(f"  Upload progress: {pct}%")

    video_id = response.get("id", "")
    logger.info(f"Upload complete. Video ID: {video_id}")
    logger.info(f"Watch: https://www.youtube.com/watch?v={video_id}")

    # Upload thumbnail if available
    if thumbnail_path and os.path.exists(thumbnail_path):
        try:
            logger.info(f"Uploading thumbnail: {thumbnail_path}")
            youtube.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(thumbnail_path),
            ).execute()
            logger.info("Thumbnail uploaded ✓")
        except Exception as exc:
            logger.warning(f"Thumbnail upload failed (non-fatal): {exc}")

    return video_id


def post_pinned_comment(video_id: str, text: str) -> None:
    """
    Post a pinned comment on the just-uploaded video.
    Non-fatal: exceptions are caught and logged as warnings.
    """
    if not video_id or not text:
        return
    try:
        youtube = _build_youtube_client()
        result  = youtube.commentThreads().insert(
            part="snippet",
            body={
                "snippet": {
                    "videoId": video_id,
                    "topLevelComment": {
                        "snippet": {"textOriginal": text},
                    },
                }
            },
        ).execute()
        comment_id = result.get("id", "")
        # Pin the comment
        youtube.comments().setModerationStatus(
            id=comment_id,
            moderationStatus="published",
        ).execute()
        logger.info(f"Pinned comment posted ✓  (comment_id={comment_id})")
    except Exception as exc:
        logger.warning(f"post_pinned_comment failed (non-fatal): {exc}")


# ---------------------------------------------------------------------------
# Phase C — playlists + session routing
# ---------------------------------------------------------------------------

def _load_playlist_cache() -> dict:
    import json
    from pathlib import Path

    path = Path(config.PLAYLISTS_STATE_PATH)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_playlist_cache(cache: dict) -> None:
    import json
    from pathlib import Path

    path = Path(config.PLAYLISTS_STATE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")


def ensure_playlist(title: str, description: str = "") -> str | None:
    """
    Create-once playlist by title; cache id in state/playlists.json.
    Returns playlist_id or None on failure.
    """
    title = (title or "").strip()
    if not title:
        return None

    cache = _load_playlist_cache()
    cached = cache.get(title)
    if cached:
        return cached

    try:
        youtube = _build_youtube_client()
        # Search existing playlists first
        page_token = None
        while True:
            resp = youtube.playlists().list(
                part="snippet",
                mine=True,
                maxResults=50,
                pageToken=page_token,
            ).execute()
            for item in resp.get("items", []) or []:
                if (item.get("snippet") or {}).get("title") == title:
                    pid = item["id"]
                    cache[title] = pid
                    _save_playlist_cache(cache)
                    logger.info(f"Playlist found: '{title}' → {pid}")
                    return pid
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

        body = {
            "snippet": {
                "title": title,
                "description": description or f"Auto playlist for {title}",
            },
            "status": {"privacyStatus": "public"},
        }
        created = youtube.playlists().insert(part="snippet,status", body=body).execute()
        pid = created.get("id")
        if pid:
            cache[title] = pid
            _save_playlist_cache(cache)
            logger.info(f"Playlist created: '{title}' → {pid}")
        return pid
    except Exception as exc:
        logger.warning(f"ensure_playlist failed (non-fatal): {exc}")
        return None


def add_video_to_playlist(playlist_id: str, video_id: str) -> bool:
    """Add video to playlist. Non-fatal. Returns True on success."""
    if not playlist_id or not video_id:
        return False
    try:
        youtube = _build_youtube_client()
        youtube.playlistItems().insert(
            part="snippet",
            body={
                "snippet": {
                    "playlistId": playlist_id,
                    "resourceId": {
                        "kind": "youtube#video",
                        "videoId": video_id,
                    },
                }
            },
        ).execute()
        logger.info(f"Added {video_id} to playlist {playlist_id}")
        return True
    except Exception as exc:
        logger.warning(f"add_video_to_playlist failed (non-fatal): {exc}")
        return False


def build_up_next_blurb(recent_video_ids: list[str] | None = None) -> str:
    """
    Description / pinned-comment blurb linking recent videos as 'Watch next'.
    """
    ids = [v for v in (recent_video_ids or []) if v]
    if not ids:
        return ""
    lines = ["Watch next:"]
    for i, vid in enumerate(ids[:3], 1):
        lines.append(f"{i}. https://www.youtube.com/watch?v={vid}")
    return "\n".join(lines)
