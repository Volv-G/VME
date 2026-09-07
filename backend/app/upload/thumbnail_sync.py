"""Background retry for thumbnails YouTube hasn't accepted yet.

`videos.insert` cannot carry a thumbnail - `thumbnails.set` is a second
call - and that call is rate limited per channel on an undocumented,
multi-hour window. Uploading a match with a dozen player reels reliably
trips it: the first few videos get their thumbnail, the rest come back
`uploadRateLimitExceeded` (HTTP 429).

Retrying by hand, one button per video, is exactly the kind of chore the
tool exists to remove. So the upload path records what YouTube refused
(`thumbnail_synced: false` in the render's `.youtube.json`) and this
worker drains that backlog in the background: a few at a time, backing
off when the limit is still in force, until every uploaded render shows
the image it was rendered with.

Deliberately modest:

* It only ever pushes an image that already exists on disk. It never
  renders, never uploads video, never touches YouTube metadata.
* It stops the moment it sees a rate-limit error, rather than hammering
  through the rest of the list to collect twelve identical failures.
* It gives up on a file after `MAX_ATTEMPTS`, because some failures are
  permanent (unverified channel) and each attempt costs 50 quota units
  out of a 10,000/day pool. Regenerating a thumbnail resets the counter,
  since that produces a genuinely new image worth trying.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Optional

from ..library import paths, scanner
from ..render.thumbnail import thumbnail_path

logger = logging.getLogger(__name__)

# How often to look for work when the last pass was uneventful.
IDLE_INTERVAL = 15 * 60.0
# First delay after a rate-limit refusal, doubled per consecutive hit.
BACKOFF_START = 20 * 60.0
BACKOFF_MAX = 4 * 60 * 60.0
# Pushes per pass. Small on purpose: the point is to trickle through the
# backlog without re-tripping the limit that created it.
BATCH = 3
# Seconds between pushes inside one pass.
SPACING = 5.0
# Per-file attempt ceiling, so a permanently refused thumbnail (e.g. an
# unverified channel) doesn't burn quota forever.
MAX_ATTEMPTS = 8


def pending() -> list[tuple[str, Path]]:
    """Uploaded renders whose thumbnail YouTube hasn't taken.

    Returns `(video_id, render_path)` pairs, oldest upload first so a
    backlog drains in the order it was created.
    """
    out: list[tuple[float, str, Path]] = []
    for team in scanner.list_teams():
        for info in scanner.list_team_full_renders(team.name):
            if not info.youtube_video_id or info.thumbnail_synced:
                continue
            render = (
                paths.renders_dir(
                    info.team, info.tournament, info.date, info.match
                )
                / info.filename
            )
            if not thumbnail_path(render).is_file():
                continue
            sidecar = scanner.load_youtube_sidecar(render) or {}
            if int(sidecar.get("thumbnail_attempts") or 0) >= MAX_ATTEMPTS:
                continue
            out.append(
                (info.youtube_uploaded_at or 0.0, info.youtube_video_id, render)
            )
    out.sort(key=lambda t: t[0])
    return [(vid, path) for _, vid, path in out]


def _record_failure(render: Path, error: str) -> None:
    """Count an attempt on the sidecar so we eventually stop trying."""
    info = scanner.load_youtube_sidecar(render)
    if info is None:
        return
    info["thumbnail_synced"] = False
    info["thumbnail_attempts"] = int(info.get("thumbnail_attempts") or 0) + 1
    info["thumbnail_error"] = error
    scanner.save_youtube_sidecar(render, info)


def _is_rate_limited(exc: Exception) -> bool:
    text = str(exc)
    return "rate-limit" in text or "rateLimit" in text or "429" in text


def run_once(limit: int = BATCH) -> tuple[int, int, bool]:
    """Try to push up to `limit` pending thumbnails.

    Returns `(pushed, failed, rate_limited)`. `rate_limited` means the
    pass stopped early because YouTube is still refusing - the caller
    should wait considerably longer before the next attempt.
    """
    from .youtube import get_status, set_thumbnail

    items = pending()
    if not items:
        return (0, 0, False)

    status = get_status()
    if not status.configured:
        logger.debug("thumbnail sync skipped: %s", status.reason)
        return (0, 0, False)

    pushed = failed = 0
    for index, (video_id, render) in enumerate(items[:limit]):
        if index:
            time.sleep(SPACING)
        thumb = thumbnail_path(render)
        try:
            set_thumbnail(video_id, thumb)
        except Exception as exc:  # noqa: BLE001 - reported, never raised
            if _is_rate_limited(exc):
                # Not this file's fault; don't count it against the
                # attempt ceiling, just come back later.
                logger.info(
                    "thumbnail sync paused: YouTube is still rate limiting "
                    "(%d pending)",
                    len(items),
                )
                return (pushed, failed, True)
            failed += 1
            _record_failure(render, str(exc))
            logger.warning(
                "thumbnail sync failed for %s (%s): %s", render.name, video_id, exc
            )
            continue
        scanner.mark_thumbnail_synced(render, True, scanner.file_digest(thumb))
        pushed += 1
        logger.info("thumbnail sync: attached %s to %s", render.name, video_id)

    remaining = len(items) - pushed
    if remaining > 0:
        logger.info("thumbnail sync: %d still pending", remaining)
    return (pushed, failed, False)


class ThumbnailSyncWorker:
    """Daemon thread that drains the pending-thumbnail backlog.

    One instance is started with the app. `wake()` lets the upload path
    say "there's new work" without waiting out the idle interval.
    """

    def __init__(self) -> None:
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._backoff = 0.0

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="thumbnail-sync", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def wake(self) -> None:
        """Ask for a pass soon (called after an upload leaves work behind)."""
        self._wake.set()

    def _loop(self) -> None:
        # Let the app finish starting before touching the media tree.
        if self._wake.wait(30.0):
            self._wake.clear()
        while not self._stop.is_set():
            delay = IDLE_INTERVAL
            try:
                pushed, _failed, limited = run_once()
                if limited:
                    self._backoff = min(
                        BACKOFF_MAX, max(BACKOFF_START, self._backoff * 2)
                    )
                    delay = self._backoff
                else:
                    self._backoff = 0.0
                    # More to do and nothing pushing back: come round
                    # again promptly rather than idling for 15 minutes.
                    delay = 60.0 if pushed else IDLE_INTERVAL
            except Exception:
                logger.warning("thumbnail sync pass failed", exc_info=True)
            if self._wake.wait(delay):
                self._wake.clear()


WORKER = ThumbnailSyncWorker()
