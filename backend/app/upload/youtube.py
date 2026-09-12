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

import json
import logging
import mimetypes
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from ..config import DATA_DIR, PROJECT_ROOT
from . import quota

logger = logging.getLogger(__name__)


class QuotaExceeded(RuntimeError):
    """The API refused a call because the daily quota pool is empty.

    Distinct from every other failure because the answer is "wait for
    the Pacific-midnight reset", not "fix something" - the upload queue
    parks the job instead of failing it.
    """


class RateLimited(RuntimeError):
    """The API refused a call because we're going too fast right now.

    Google has several burst buckets that are separate from the daily
    quota pool and are documented nowhere useful: uploads per channel
    per window, thumbnails per channel per window, plain per-user rate.
    They refill in minutes to hours, and the correct response is always
    the same - come back later with the same request. Distinct from
    `QuotaExceeded` (a whole day) and from a real failure (fix
    something), because the upload queue treats all three differently.

    `retry_after` is a hint in seconds for callers that park the work:
    the channel video-count cap needs hours, a plain burst needs
    minutes, and using one wait for both either hammers the API or
    stalls an upload that could have gone an hour ago.
    """

    def __init__(self, message: str, *, retry_after: Optional[float] = None):
        super().__init__(message)
        self.retry_after = retry_after


def _is_upload_limit_error(exc: Exception) -> bool:
    """Does this mean "this channel has published too many videos"?

    A DIFFERENT limit from everything else here, and the one that
    actually stops a match day: YouTube caps how many videos a channel
    may publish in a rolling window and answers

        400 uploadLimitExceeded
        "The user has exceeded the number of videos they may upload."

    Note the 400 - not 403, not 429 - and note that the reason string
    `uploadLimitExceeded` is NOT a substring of `uploadRateLimitExceeded`
    or `rateLimitExceeded`, so it has to be matched on its own or it
    reads as an unretryable client error and fails the job.

    Nothing in the API reports this cap; only a rejection reveals it.
    """
    return "uploadLimitExceeded" in str(exc)


def _is_rate_limit_error(exc: Exception) -> bool:
    """Does this HttpError mean "too fast, try again later"?

    Explicitly NOT `quotaExceeded`: that one is a day-long wait and has
    its own exception. `uploadRateLimitExceeded` arrives as a 403 with
    the reason in the body rather than a 429, which is why the text is
    checked as well as the status.
    """
    if _is_quota_error(exc):
        return False
    if _is_upload_limit_error(exc):
        return True
    if getattr(getattr(exc, "resp", None), "status", 0) == 429:
        return True
    text = str(exc)
    return any(
        reason in text
        for reason in (
            "uploadRateLimitExceeded",
            "rateLimitExceeded",
            "userRateLimitExceeded",
        )
    )


def _is_quota_error(exc: Exception) -> bool:
    """Does this HttpError mean "daily quota gone"?

    Google answers 403 with reason `quotaExceeded` for the daily pool and
    `rateLimitExceeded` / `uploadRateLimitExceeded` for the much shorter
    burst buckets - only the first is a day-long wait, so they must not
    be conflated.
    """
    text = str(exc)
    return "quotaExceeded" in text and "uploadRateLimitExceeded" not in text


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


def auth_error_path() -> Path:
    """Marker file recording the last unrecoverable auth failure.

    `get_status()` deliberately makes no network calls (it's polled on
    every page load), so a revoked/expired refresh token would otherwise
    keep showing "ready" until the user tried an upload and got a raw
    `invalid_grant` tuple. `_build_service()` drops this marker when a
    refresh fails for good and removes it as soon as auth works again,
    which gives the UI something truthful to display for free.
    """
    return _config_dir() / "youtube_auth_error.json"


REAUTHORIZE_HINT = (
    "Re-run `python -m scripts.yt_authorize` from backend/ "
    "(or scripts/yt-authorize.ps1) to re-authorize."
)


def _record_auth_error(message: str) -> None:
    try:
        auth_error_path().write_text(
            json.dumps({"error": message, "at": time.time()}, indent=2),
            encoding="utf-8",
        )
    except OSError:
        logger.debug("could not persist auth error marker", exc_info=True)


def _clear_auth_error() -> None:
    try:
        auth_error_path().unlink(missing_ok=True)
    except OSError:
        logger.debug("could not clear auth error marker", exc_info=True)


def _stale_auth_error() -> Optional[str]:
    """Last auth error, unless the token file has been rewritten since.

    Comparing mtimes means a successful `yt_authorize` run clears the
    warning without the script having to know about this marker.
    """
    marker = auth_error_path()
    if not marker.is_file():
        return None
    tok = token_path()
    try:
        if tok.is_file() and tok.stat().st_mtime > marker.stat().st_mtime:
            return None
        return str(json.loads(marker.read_text(encoding="utf-8")).get("error") or "")
    except (OSError, json.JSONDecodeError):
        return None


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
    stale = _stale_auth_error()
    if stale:
        return ConfigStatus(
            configured=False,
            reason=stale,
            has_client_secret=True,
            has_token=True,
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
        from google.auth.exceptions import RefreshError
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
            try:
                creds.refresh(Request())
            except RefreshError as exc:
                # `invalid_grant` is by far the most common failure and
                # its raw form (("invalid_grant: Bad Request", {...}))
                # tells the user nothing. The refresh token is gone for
                # good in every one of these cases - only re-consent
                # fixes it - so record it and explain why.
                detail = str(exc)
                if "invalid_grant" in detail:
                    msg = (
                        "YouTube authorization has expired or been revoked "
                        f"(invalid_grant; token issued {_token_issued_hint(tok)}). "
                        "Google expires refresh tokens after 7 days while the "
                        "OAuth consent screen is in 'Testing' publishing status, "
                        "and also drops them if access was revoked or the "
                        "client secret was rotated. " + REAUTHORIZE_HINT
                    )
                else:
                    msg = (
                        f"Could not refresh YouTube credentials: {detail}. "
                        + REAUTHORIZE_HINT
                    )
                _record_auth_error(msg)
                raise RuntimeError(msg) from exc
            # Persist the rotated access token so subsequent uploads
            # don't need a fresh network round-trip.
            tok.write_text(creds.to_json(), encoding="utf-8")
        else:
            msg = (
                "Saved YouTube credentials are invalid and carry no refresh "
                "token. " + REAUTHORIZE_HINT
            )
            _record_auth_error(msg)
            raise RuntimeError(msg)
    _clear_auth_error()
    # `cache_discovery=False` silences a noisy file-cache warning under
    # newer google-api-python-client versions.
    return build("youtube", "v3", credentials=creds, cache_discovery=False)


def set_thumbnail(video_id: str, image_path: Path) -> None:
    """Attach a custom thumbnail to `video_id` (`thumbnails.set`).

    Costs 50 quota units, and requires the channel to be verified - an
    unverified channel gets HTTP 403 with reason
    `forbidden`/`uploadLimitExceeded`, which we translate into a message
    the user can act on instead of a raw API dump.

    Also used to REPLACE an existing thumbnail: the API has no separate
    update call, `set` overwrites whatever is there.

    Raises RuntimeError on failure so callers can decide whether that's
    fatal (the regenerate endpoint reports it) or not (an upload job
    just warns).
    """
    from googleapiclient.errors import HttpError  # type: ignore
    from googleapiclient.http import MediaFileUpload  # type: ignore

    if not image_path.is_file():
        raise RuntimeError(f"Thumbnail not found: {image_path}")
    size = image_path.stat().st_size
    if size > 2 * 1024 * 1024:
        raise RuntimeError(
            f"Thumbnail is {size / 1024 / 1024:.1f} MB; YouTube's limit is 2 MB"
        )

    youtube = _build_service()
    mimetype = mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
    try:
        youtube.thumbnails().set(
            videoId=video_id,
            media_body=MediaFileUpload(str(image_path), mimetype=mimetype),
        ).execute()
        quota.record("thumbnails.set", note=video_id)
    except HttpError as exc:
        status = getattr(exc.resp, "status", 0)
        if _is_quota_error(exc):
            quota.mark_exhausted(f"thumbnails.set {video_id}")
            raise QuotaExceeded(
                "The YouTube API daily quota is exhausted, so the thumbnail "
                f"could not be pushed. It resets {quota.format_reset()}; the "
                "local image is already correct and will be retried."
            ) from exc
        if status == 403:
            raise RuntimeError(
                "YouTube refused the custom thumbnail (403). Custom "
                "thumbnails require a verified channel - verify at "
                "youtube.com/verify, then try again."
            ) from exc
        if status == 404:
            raise RuntimeError(
                f"Video {video_id} not found - was it deleted on YouTube?"
            ) from exc
        if status == 429 or "uploadRateLimitExceeded" in str(exc):
            # Separate, undocumented bucket from the upload quota, and it
            # refills over hours. Nothing to fix - the local image is
            # already correct, it just has to be pushed again later.
            raise RateLimited(
                "YouTube is rate-limiting thumbnail uploads right now "
                "(too many in a short window). The local thumbnail was "
                "updated - push it again in a few hours."
            ) from exc
        raise RuntimeError(f"YouTube API error setting thumbnail: {exc}") from exc


def video_exists(video_id: str) -> Optional[bool]:
    """Does `video_id` still exist on the authorized channel?

    Returns True / False, or None when the question can't be answered
    (uploads not configured, auth expired, network error) so callers can
    tell "definitely gone" from "couldn't check".

    `videos.list` costs 1 quota unit, i.e. it is effectively free next to
    the 10,000/day pool - safe to call from a UI action.
    """
    if not video_id:
        return None
    try:
        youtube = _build_service()
        resp = youtube.videos().list(part="id", id=video_id).execute()
        quota.record("videos.list", note=video_id)
    except Exception as exc:
        logger.warning("could not verify YouTube video %s: %s", video_id, exc)
        return None
    return bool(resp.get("items"))


@dataclass
class UploadResult:
    video_id: str
    video_url: str  # https://youtu.be/<id>
    # The privacyStatus YouTube actually applied to the upload, read
    # back from the videos.insert response. Will differ from the
    # requested value when Google silently downgrades the upload (see
    # `upload_video` for the OAuth test-mode caveat).
    actual_privacy_status: str = ""
    requested_privacy_status: str = ""


def _token_issued_hint(tok: Path) -> str:
    """Human-readable age of the token file, for the expiry message."""
    try:
        age_days = (time.time() - tok.stat().st_mtime) / 86400.0
        return f"{age_days:.0f} days ago"
    except OSError:
        return "at an unknown time"


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

    # Derive the MIME type from the extension: the render container is
    # configurable now (HandBrake preset -> mp4 / mkv / mov), and
    # hardcoding video/mp4 mislabels a .mov/.mkv upload.
    mimetype = mimetypes.guess_type(file_path.name)[0] or "video/*"
    if not mimetype.startswith("video/"):
        mimetype = "video/*"

    media = MediaFileUpload(
        str(file_path),
        mimetype=mimetype,
        chunksize=chunk_size_mb * 1024 * 1024,
        resumable=True,
    )
    request = youtube.videos().insert(
        part="snippet,status", body=body, media_body=media
    )
    # Charge the insert BEFORE sending. Google bills the call, not its
    # outcome, and an upload that dies on chunk 300 of 600 has already
    # cost the 1,600 units - counting it only on success would let the
    # queue start a second doomed upload straight after.
    quota.record("videos.insert", note=file_path.name)

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
            if _is_quota_error(exc):
                quota.mark_exhausted(f"videos.insert {file_path.name}")
                raise QuotaExceeded(
                    "The YouTube API daily quota ran out mid-upload. It "
                    f"resets {quota.format_reset()} - the queue will retry "
                    "then."
                ) from exc
            if _is_rate_limit_error(exc):
                # A burst bucket or the channel's video-count cap -
                # either way no video was created, so take the 1,600
                # units back: otherwise a few refused attempts read as
                # "quota nearly gone" when nothing was uploaded at all.
                # The bytes we sent are lost (no resumable handle
                # survives the process), but the answer is simply
                # "later" - the caller parks the job rather than
                # failing it.
                quota.refund(
                    "videos.insert",
                    note=f"refused: {file_path.name}",
                )
                if _is_upload_limit_error(exc):
                    until = quota.mark_upload_limit(note=file_path.name)
                    raise RateLimited(
                        "YouTube says this channel has published as many "
                        "videos as it currently allows, so it refused the "
                        "upload. This is a channel limit, not the API "
                        "quota - it frees up as earlier uploads age out. "
                        "The upload stays queued and will be retried "
                        f"{quota.format_wait(until - time.time())}.",
                        retry_after=max(0.0, until - time.time()),
                    ) from exc
                raise RateLimited(
                    "YouTube is rate-limiting uploads on this channel right "
                    "now (too many videos in a short window). The upload "
                    "will be retried automatically."
                ) from exc
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

    # An accepted video is proof the channel cap has reopened - better
    # evidence than the cooldown guess, so drop it and let the rest of
    # the queue run.
    quota.clear_upload_limit()

    # YouTube silently downgrades `privacyStatus` to `private` when the
    # OAuth client is in Google Cloud Console's "Testing" publishing
    # status, OR when the channel hasn't completed phone verification.
    # The API does NOT return an error in either case - the upload
    # succeeds and the video just sits as private. Read back the actual
    # value so we can log a loud warning and store the truth in the
    # sidecar; users were otherwise discovering this hours later when
    # they checked the channel.
    actual_privacy = (
        ((response or {}).get("status") or {}).get("privacyStatus")
        or privacy_status
    )
    if actual_privacy != privacy_status:
        logger.warning(
            "YouTube downgraded privacyStatus %r -> %r for video %s. "
            "This usually means the OAuth client is in 'Testing' mode "
            "(Google Cloud Console -> OAuth consent screen -> Publishing "
            "status: In production) OR the YouTube channel isn't "
            "phone-verified. Until one of those is fixed, every upload "
            "will land as 'private'.",
            privacy_status,
            actual_privacy,
            video_id,
        )
    else:
        logger.info(
            "YouTube upload %s succeeded with privacyStatus=%r",
            video_id,
            actual_privacy,
        )
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
            quota.record("playlistItems.insert", note=playlist_id)
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
        actual_privacy_status=actual_privacy,
        requested_privacy_status=privacy_status,
    )
