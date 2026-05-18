"""YouTube Data API v3 wrapper: OAuth token loading + resumable upload.

Files this module reads / writes (under `${VME_DATA_DIR}` or the
project root):

  youtube_client_secret.json    OAuth 2.0 client credentials downloaded
                                from Google Cloud Console. Type: "Desktop
                                application". Created once by the user.
  youtube_token.json            User refresh + access tokens. Created by
                                the one-time `scripts/yt_authorize.py`
                                run and refreshed automatically thereafter.

Why lazy imports: the google-api-python-client stack is heavy and not
needed for renders / scheduling. `is_configured()` returns False with a
human-readable reason when the package is missing, so the UI can grey
out the upload button without crashing the rest of the app.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from ..config import DATA_DIR, PROJECT_ROOT

logger = logging.getLogger(__name__)


# Scopes we request during the one-time consent flow. `youtube.upload`
# is enough for `videos.insert`; we additionally need `youtube` (the
# read/manage scope) to attach uploaded videos to a playlist. Adding
# both up-front avoids forcing a re-consent if the user later configures
# a playlist.
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]


def _config_dir() -> Path:
    """Where token + client-secret files live.

    Pinned to `DATA_DIR` when `VME_DATA_DIR` is set; otherwise the
    project root for legacy repo-local installs. Same resolution as
    `jobs.json` in app/jobs/persistence.py.
    """
    return DATA_DIR if DATA_DIR is not None else PROJECT_ROOT


def client_secrets_path() -> Path:
    return _config_dir() / "youtube_client_secret.json"


def token_path() -> Path:
    return _config_dir() / "youtube_token.json"


@dataclass
class ConfigStatus:
    """Snapshot of upload-feature readiness, returned to the UI.

    The frontend uses `configured` to decide whether the "Upload to
    YouTube" button is enabled. `reason` carries a human-readable message
    used as a tooltip / error so the user knows what's missing.
    """

    configured: bool
    reason: str
    has_client_secret: bool
    has_token: bool
    library_installed: bool


def get_status() -> ConfigStatus:
    """Check that the uploader is ready without performing a network call.

    Returns `configured=True` iff the google-* libraries are importable
    AND both the client-secret and token files exist. We deliberately
    don't try to refresh the token here - that's the upload-job's
    responsibility - so this stays cheap enough to poll on every page
    load.
    """
    try:
        import googleapiclient  # noqa: F401
        import google_auth_oauthlib  # noqa: F401
        import google.oauth2.credentials  # noqa: F401

        lib_ok = True
    except ImportError:
        lib_ok = False

    cs = client_secrets_path().is_file()
    tok = token_path().is_file()

    if not lib_ok:
        return ConfigStatus(
            configured=False,
            reason=(
                "YouTube upload libraries are not installed. "
                "Run `pip install -e .` in backend/ to pick up the "
                "new dependencies."
            ),
            has_client_secret=cs,
            has_token=tok,
            library_installed=False,
        )
    if not cs:
        return ConfigStatus(
            configured=False,
            reason=(
                f"Missing OAuth client secret at {client_secrets_path()}. "
                "Create a Desktop OAuth client in Google Cloud Console, "
                "download the JSON, and save it to that path."
            ),
            has_client_secret=False,
            has_token=tok,
            library_installed=True,
        )
    if not tok:
        return ConfigStatus(
            configured=False,
            reason=(
                "YouTube account not authorized yet. Run "
                "`python -m scripts.yt_authorize` from backend/ to "
                "complete the one-time OAuth flow."
            ),
            has_client_secret=True,
            has_token=False,
            library_installed=True,
        )
    return ConfigStatus(
        configured=True,
        reason="Ready.",
        has_client_secret=True,
        has_token=True,
        library_installed=True,
    )


def _build_service():
    """Create an authenticated `youtube` API client.

    Loads the saved refresh token, mints a fresh access token if
    necessary (and re-saves the token file when google-auth rotates
    fields), and returns the v3 client. Raises a clear RuntimeError
    when prerequisites aren't met - caller is expected to surface this
    as a user-facing failure message.
    """
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError(
            "YouTube upload libraries are not installed: " + str(exc)
        )

    tok = token_path()
    if not tok.is_file():
        raise RuntimeError(
            f"No saved YouTube token at {tok}. "
            "Run `python -m scripts.yt_authorize` first."
        )
    creds = Credentials.from_authorized_user_file(str(tok), SCOPES)
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            # Persist the rotated access token so subsequent uploads
            # don't need a fresh network round-trip.
            tok.write_text(creds.to_json(), encoding="utf-8")
        else:
            raise RuntimeError(
                "Saved YouTube credentials are invalid and cannot be "
                "refreshed. Re-run `python -m scripts.yt_authorize`."
            )
    # `cache_discovery=False` silences a noisy file-cache warning under
    # newer google-api-python-client versions.
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


@dataclass
class UploadResult:
    video_id: str
    video_url: str  # https://youtu.be/<id>


def upload_video(
    *,
    file_path: Path,
    title: str,
    description: str,
    privacy_status: str = "unlisted",
    tags: Optional[list[str]] = None,
    playlist_id: Optional[str] = None,
    progress_cb: Optional[Callable[[float, int, int], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
    chunk_size_mb: int = 8,
) -> UploadResult:
    """Upload `file_path` to YouTube with resumable multi-chunk upload.

    Progress is reported via `progress_cb(percent, bytes_done,
    bytes_total)` after each chunk. The caller may also pass a
    `cancel_check()` that returns True to abort between chunks - the
    partial upload is dropped on the YouTube side (a fresh upload would
    have to start over; YouTube doesn't expose a way to keep a partial
    session beyond the lifetime of the script).

    `chunk_size_mb` defaults to 8 MB - small enough to give the progress
    bar smooth motion on residential upstream, large enough that HTTP
    overhead doesn't dominate. YouTube requires chunks be multiples of
    256 KB; we multiply MB by 1024*1024 which satisfies that.

    Raises RuntimeError on any non-recoverable failure (auth issue,
    network error after retry budget exhausted, API rejection).
    """
    from googleapiclient.errors import HttpError  # type: ignore
    from googleapiclient.http import MediaFileUpload  # type: ignore

    if not file_path.is_file():
        raise RuntimeError(f"Render file not found: {file_path}")
    if privacy_status not in {"private", "unlisted", "public"}:
        raise RuntimeError(
            f"Invalid privacyStatus {privacy_status!r}; "
            "must be one of private / unlisted / public."
        )

    youtube = _build_service()

    body: dict = {
        "snippet": {
            "title": title[:100],  # YouTube hard limit on title length
            "description": description[:5000],  # hard limit on description
            "tags": (tags or [])[:500],
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False,
        },
    }

    media = MediaFileUpload(
        str(file_path),
        mimetype="video/mp4",
        chunksize=chunk_size_mb * 1024 * 1024,
        resumable=True,
    )
    request = youtube.videos().insert(
        part="snippet,status", body=body, media_body=media
    )

    total = file_path.stat().st_size
    response = None
    # Per Google's docs: call `next_chunk()` in a loop. It returns
    # (status, response) - status is None until enough chunks are sent
    # to make headway, then `response` is non-None on the final chunk.
    # Each call sends one chunk.
    retries = 0
    MAX_RETRIES = 5
    while response is None:
        if cancel_check and cancel_check():
            raise RuntimeError("Upload cancelled by user")
        try:
            status, response = request.next_chunk()
        except HttpError as exc:
            # 5xx errors are commonly retryable; 4xx aren't. We bail on
            # 4xx immediately to surface bad-data / auth issues without
            # eating retries.
            status_code = getattr(exc.resp, "status", 0)
            if 500 <= status_code < 600 and retries < MAX_RETRIES:
                retries += 1
                wait = 2**retries
                logger.warning(
                    "YouTube %d on chunk; retry %d/%d after %ds",
                    status_code,
                    retries,
                    MAX_RETRIES,
                    wait,
                )
                time.sleep(wait)
                continue
            raise RuntimeError(f"YouTube API error: {exc}") from exc
        if status is not None and progress_cb is not None:
            done = int(status.resumable_progress)
            progress_cb(min(100.0, done / max(1, total) * 100.0), done, total)

    video_id = (response or {}).get("id")
    if not video_id:
        raise RuntimeError(f"YouTube did not return a video id: {response!r}")
    # Final progress tick - the last chunk's response sometimes skips a
    # progress callback because `status` was None on completion.
    if progress_cb is not None:
        progress_cb(100.0, total, total)

    # Best-effort playlist attach. We treat a playlist failure as a
    # WARNING, not a hard error: the video uploaded successfully and the
    # user can move it manually. Otherwise a typo in `playlist_id`
    # would mark the whole job failed even though the upload worked.
    if playlist_id:
        try:
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
        except HttpError as exc:
            logger.warning(
                "uploaded video %s but failed to add to playlist %s: %s",
                video_id,
                playlist_id,
                exc,
            )

    return UploadResult(
        video_id=video_id,
        video_url=f"https://youtu.be/{video_id}",
    )
