"""YouTube Data API client: OAuth, upload, pinned comment, playlists.

Ported from services/youtube_service.py. Construction never touches the
network or file system; OAuth and the API client are created on first use.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

from yt_video.logging import get_logger
from yt_video.memory._io import read_json, write_json_atomic
from yt_video.memory.analytics_memory import ANALYTICS_SCOPE

logger = get_logger()

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
    ANALYTICS_SCOPE,
]

# Keep defaults niche-neutral. Never tag "AI generated / AI voice" — that
# brands uploads as low-trust filler and tanks CTR in browse/search.
_DEFAULT_TAGS = [
    "trending topics", "India news", "explainer",
    "educational", "YouTube India",
]

_AI_DISCLOSURE = (
    "\n\n---\n"
    "This video uses AI-assisted script writing and AI-generated voice narration. "
    "For educational purposes only."
)

_BLOCKED_TAGS = {"ai generated", "ai voice", "ai narration", "ai-generated"}


def _sanitize_title(title: str) -> str:
    replacements = {"▲": "+", "▼": "-", "△": "+", "▽": "-", "<": "(", ">": ")"}
    for char, sub in replacements.items():
        title = title.replace(char, sub)
    title = title.strip()[:100]
    return title or "Untitled"


class YouTubeClient:
    def __init__(
        self,
        client_secret_path: Path,
        token_path: Path,
        playlists_state_path: Path,
        channel_name: str | None = None,
    ) -> None:
        self._client_secret_path = Path(client_secret_path)
        self._token_path = Path(token_path)
        self._playlists_state_path = Path(playlists_state_path)
        self._channel_name = channel_name
        self._creds: Credentials | None = None
        self._youtube: Any = None
        self._youtube_creds: Credentials | None = None

    # ------------------------------------------------------------------ auth
    def _save_token(self, creds: Credentials) -> None:
        write_json_atomic(self._token_path, json.loads(creds.to_json()))

    def credentials(self) -> Credentials:
        """Load, refresh or (browser flow) obtain OAuth credentials with all SCOPES."""
        if self._creds is not None and self._creds.valid:
            return self._creds

        creds = None
        if self._token_path.exists():
            try:
                creds = Credentials.from_authorized_user_file(str(self._token_path))
            except Exception:
                creds = None

        if creds is not None and not set(SCOPES) <= set(creds.scopes or []):
            logger.info("Cached YouTube token is missing a required scope — re-authorising")
            creds = None

        if creds is not None and creds.valid:
            self._creds = creds
            return creds

        if creds is not None and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                self._save_token(creds)
                self._creds = creds
                return creds
            except Exception:
                pass

        logger.info("Starting YouTube OAuth flow...")
        flow = InstalledAppFlow.from_client_secrets_file(str(self._client_secret_path), SCOPES)
        creds = flow.run_local_server(port=0)
        logger.info("OAuth authentication successful")
        self._save_token(creds)
        self._creds = creds
        return creds

    def _api(self) -> Any:
        """Lazily build and cache the googleapiclient resource."""
        creds = self.credentials()
        if self._youtube is None or self._youtube_creds is not creds:
            self._youtube = build("youtube", "v3", credentials=creds)
            self._youtube_creds = creds
        return self._youtube

    # ---------------------------------------------------------------- upload
    def upload_video(
        self,
        title: str,
        description: str,
        privacy_status: str,
        thumbnail_path: Path | None,
        tags: list[str],
        video_path: Path,
        category_id: str,
    ) -> str | None:
        """Upload a video (and thumbnail if present). Returns the video id."""
        title = _sanitize_title(title)
        youtube = self._api()

        cleaned: list[str] = []
        seen: set[str] = set()
        default_tags = _DEFAULT_TAGS + [self._channel_name or ""]
        for raw in (tags or []) + default_tags:
            tag = (raw or "").strip()
            if not tag:
                continue
            key = tag.lower()
            if key in _BLOCKED_TAGS or key in seen:
                continue
            seen.add(key)
            cleaned.append(tag)

        body = {
            "snippet": {
                "title": title,
                "description": description + _AI_DISCLOSURE,
                "categoryId": (category_id or "22").strip(),
                "tags": cleaned[:30],
            },
            "status": {
                "privacyStatus": privacy_status,
                "selfDeclaredMadeForKids": False,
            },
        }

        media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True)
        logger.info(f"Uploading video: '{title}' (privacy={privacy_status})")
        request = youtube.videos().insert(
            part="snippet,status", body=body, media_body=media
        )

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                logger.info(f"  Upload progress: {int(status.progress() * 100)}%")

        video_id = response.get("id", "")
        logger.info(f"Upload complete. Video ID: {video_id}")
        logger.info(f"Watch: https://www.youtube.com/watch?v={video_id}")

        if thumbnail_path and Path(thumbnail_path).exists():
            try:
                logger.info(f"Uploading thumbnail: {thumbnail_path}")
                youtube.thumbnails().set(
                    videoId=video_id,
                    media_body=MediaFileUpload(str(thumbnail_path)),
                ).execute()
                logger.info("Thumbnail uploaded")
            except Exception as exc:
                logger.warning(f"Thumbnail upload failed (non-fatal): {exc}")

        return video_id or None

    def post_pinned_comment(self, video_id: str, text: str) -> None:
        """Post a comment and publish it. Non-fatal: failures log a warning."""
        if not video_id or not text:
            return
        try:
            youtube = self._api()
            result = youtube.commentThreads().insert(
                part="snippet",
                body={
                    "snippet": {
                        "videoId": video_id,
                        "topLevelComment": {"snippet": {"textOriginal": text}},
                    }
                },
            ).execute()
            comment_id = result.get("id", "")
            youtube.comments().setModerationStatus(
                id=comment_id, moderationStatus="published"
            ).execute()
            logger.info(f"Pinned comment posted (comment_id={comment_id})")
        except Exception as exc:
            logger.warning(f"post_pinned_comment failed (non-fatal): {exc}")

    # ------------------------------------------------------------- playlists
    def _load_playlist_cache(self) -> dict:
        return read_json(self._playlists_state_path, dict) or {}

    def _save_playlist_cache(self, cache: dict) -> None:
        write_json_atomic(self._playlists_state_path, cache, ensure_ascii=False)

    def ensure_playlist(self, title: str, description: str = "") -> str | None:
        """Create-once playlist by title; cache the id. None on failure."""
        title = (title or "").strip()
        if not title:
            return None

        cache = self._load_playlist_cache()
        cached = cache.get(title)
        if cached:
            return cached

        try:
            youtube = self._api()
            page_token = None
            while True:
                resp = youtube.playlists().list(
                    part="snippet", mine=True, maxResults=50, pageToken=page_token
                ).execute()
                for item in resp.get("items", []) or []:
                    if (item.get("snippet") or {}).get("title") == title:
                        pid = item["id"]
                        cache[title] = pid
                        self._save_playlist_cache(cache)
                        logger.info(f"Playlist found: '{title}' -> {pid}")
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
                self._save_playlist_cache(cache)
                logger.info(f"Playlist created: '{title}' -> {pid}")
            return pid
        except Exception as exc:
            logger.warning(f"ensure_playlist failed (non-fatal): {exc}")
            return None

    def add_video_to_playlist(self, playlist_id: str, video_id: str) -> bool:
        """Add video to playlist. Non-fatal. Returns True on success."""
        if not playlist_id or not video_id:
            return False
        try:
            self._api().playlistItems().insert(
                part="snippet",
                body={
                    "snippet": {
                        "playlistId": playlist_id,
                        "resourceId": {"kind": "youtube#video", "videoId": video_id},
                    }
                },
            ).execute()
            logger.info(f"Added {video_id} to playlist {playlist_id}")
            return True
        except Exception as exc:
            logger.warning(f"add_video_to_playlist failed (non-fatal): {exc}")
            return False
