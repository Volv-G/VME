"""YouTube *live broadcast* management (`liveBroadcasts`, `liveStreams`).

Why this exists
---------------
The phone streams to a **persistent stream key**. YouTube will happily
create a broadcast by itself when bytes arrive on that key, which is
exactly the right behaviour at a gym - press one button, be live - but
every broadcast so created inherits the one title saved on the stream.
Left alone, a season lands on the channel as a column of identically
named videos, which is precisely what the render pipeline's title
templates exist to prevent.

So VME prepares the broadcast *before* the match: title, description and
thumbnail from the same templates and the same generator the uploads
use, bound to the stream key the phone already has.

The key never leaves the phone and never changes
------------------------------------------------
Binding a new broadcast to the **existing** stream is what makes this
safe. The alternative - minting a fresh stream per match and shipping
its key to the phone - would need a credential channel to a device on
gym wi-fi, which is a worse problem than the one it solves. Here the
OAuth token stays on the PC where it already lives, and the phone's
configuration is a constant.

For the same reason `list_ingest_streams()` returns only the **last four
characters** of each key: enough to confirm the phone is pointed at the
right stream, useless to anyone who reads it off a screen or a log.

Settings that are not preferences
---------------------------------
Three fields decide whether this works at all, and all three have a
default that is wrong for us:

* `enableAutoStop=False` - with auto-stop on, one dropout **ends the
  broadcast permanently** and the encoder's reconnect has nowhere to go.
  A 65-second dropout is already on record for this rig (see
  docs/android-streaming-spike.md), so this is not hypothetical.
* `monitorStream.enableMonitorStream=False` - the monitor ("testing")
  phase otherwise holds the stream in preview until somebody presses a
  button in Studio. Nobody is at a computer during a match.
* `status.selfDeclaredMadeForKids` - **required**, not optional. Omit it
  and the insert fails outright.

`latencyPreference` is "low" rather than "ultraLow" deliberately:
ultra-low disables DVR, and the recording is the point (step 4 of the
spike plan has the archive replacing the expensive full-match upload).
"""

from __future__ import annotations

import datetime as dt
import logging
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from . import quota

# Same package, and deliberately shared: `_build_service` carries the
# token refresh, the `invalid_grant` diagnosis and the auth-error
# sidecar. A second copy of that logic would drift from the first.
from .youtube import (  # noqa: F401  (QuotaExceeded re-exported for callers)
    QuotaExceeded,
    RateLimited,
    _build_service,
    _is_quota_error,
)

logger = logging.getLogger("vme.live")

# YouTube's own ingest endpoints. The primary is what the phone uses;
# the backup exists for encoders that can push both.
INGEST_PRIMARY = "rtmps://a.rtmps.youtube.com:443/live2"
INGEST_BACKUP = "rtmps://b.rtmps.youtube.com:443/live2?backup=1"

WATCH_URL = "https://www.youtube.com/watch?v="

VALID_PRIVACY = ("public", "unlisted", "private")


@dataclass
class IngestStream:
    """A reusable ingest endpoint on the channel (a "stream key")."""

    id: str
    title: str
    # Last four characters of the key only - see module docstring.
    key_tail: str
    is_reusable: bool
    stream_status: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "key_tail": self.key_tail,
            "is_reusable": self.is_reusable,
            "stream_status": self.stream_status,
        }


@dataclass
class Broadcast:
    """A prepared (or live, or finished) broadcast."""

    id: str
    title: str
    description: str
    privacy_status: str
    life_cycle_status: str
    scheduled_start: Optional[str]
    bound_stream_id: Optional[str]
    watch_url: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "privacy_status": self.privacy_status,
            "life_cycle_status": self.life_cycle_status,
            "scheduled_start": self.scheduled_start,
            "bound_stream_id": self.bound_stream_id,
            "watch_url": self.watch_url,
        }


def _translate(exc: Exception, what: str) -> Exception:
    """Turn an HttpError into something a user can act on.

    Live-specific failures are their own vocabulary and the raw API dump
    explains none of them - `liveStreamingNotEnabled` in particular looks
    like a bug in VME when it is a one-time switch on the channel.
    """
    from googleapiclient.errors import HttpError  # type: ignore

    if not isinstance(exc, HttpError):
        return RuntimeError(f"{what} failed: {exc}")

    status = getattr(exc.resp, "status", 0)
    detail = str(exc)

    if _is_quota_error(exc):
        quota.mark_exhausted(what)
        return QuotaExceeded(
            f"The YouTube API daily quota is exhausted, so {what} failed. "
            f"It resets {quota.format_reset()}."
        )
    if "liveStreamingNotEnabled" in detail:
        return RuntimeError(
            "Live streaming is not enabled on this channel yet. Enable it at "
            "youtube.com/livestreaming - it needs phone verification and then "
            "takes up to 24 hours to activate the first time."
        )
    if "insufficientLivePermissions" in detail or "livePermissionDenied" in detail:
        return RuntimeError(
            "The authorized account may not manage live broadcasts on this "
            "channel. Check that the token belongs to the channel owner."
        )
    if "invalidScheduledStartTime" in detail:
        return RuntimeError(
            "YouTube refused the scheduled start time. It must be in the "
            "future and in RFC 3339 form."
        )
    if "errorStreamInactive" in detail:
        return RuntimeError(
            "That ingest stream has no encoder connected, so YouTube will not "
            "transition the broadcast. Start the phone first."
        )
    if status == 429 or "rateLimitExceeded" in detail:
        return RateLimited(
            f"YouTube is rate-limiting live API calls right now ({what}). "
            "Wait a few minutes and try again."
        )
    return RuntimeError(f"YouTube API error during {what}: {exc}")


def list_ingest_streams() -> list[IngestStream]:
    """Reusable ingest endpoints on the channel (`liveStreams.list`, 1 unit)."""
    youtube = _build_service()
    try:
        resp = (
            youtube.liveStreams()
            .list(part="id,snippet,cdn,status", mine=True, maxResults=50)
            .execute()
        )
        quota.record("liveStreams.list", note="mine")
    except Exception as exc:  # noqa: BLE001 - translated immediately
        raise _translate(exc, "listing ingest streams") from exc

    out: list[IngestStream] = []
    for item in resp.get("items", []):
        cdn = item.get("cdn", {}) or {}
        info = cdn.get("ingestionInfo", {}) or {}
        key = info.get("streamName", "") or ""
        out.append(
            IngestStream(
                id=item.get("id", ""),
                title=(item.get("snippet", {}) or {}).get("title", ""),
                key_tail=key[-4:] if key else "",
                is_reusable=bool(cdn.get("isReusable", True)),
                stream_status=(item.get("status", {}) or {}).get(
                    "streamStatus", "unknown"
                ),
            )
        )
    return out


def find_stream(*, stream_id: Optional[str], key_tail: Optional[str]) -> IngestStream:
    """Resolve the stream to bind to, by id or by the last 4 of its key.

    Refuses ambiguity rather than picking one: binding the wrong stream
    means the phone streams into a broadcast nobody is watching, and
    that failure is invisible until someone checks the channel.
    """
    streams = list_ingest_streams()
    if not streams:
        raise RuntimeError(
            "This channel has no reusable stream key. Create one in YouTube "
            "Studio (Create -> Go live -> Stream) before preparing broadcasts."
        )
    if stream_id:
        for s in streams:
            if s.id == stream_id:
                return s
        raise RuntimeError(f"No ingest stream with id {stream_id} on this channel.")
    if key_tail:
        tail = key_tail.strip()[-4:]
        matches = [s for s in streams if s.key_tail == tail]
        if not matches:
            raise RuntimeError(
                f"No ingest stream whose key ends in '{tail}'. The phone may be "
                "configured with a key from a different channel."
            )
        if len(matches) > 1:
            raise RuntimeError(
                f"{len(matches)} ingest streams end in '{tail}' - pass an "
                "explicit stream_id to disambiguate."
            )
        return matches[0]
    if len(streams) > 1:
        raise RuntimeError(
            f"This channel has {len(streams)} stream keys and none was "
            "specified. Pass stream_id (or the last 4 characters of the key "
            "the phone uses) so the broadcast binds to the right one."
        )
    return streams[0]


# `scheduledStartTime` is required by `liveBroadcasts.insert`, but this
# rig has nothing to schedule: the broadcast is prepared minutes before
# the whistle and goes live the moment the phone connects. So the field
# is filled in with "now, plus a small cushion" and never exposed as a
# choice. The cushion exists because YouTube rejects a start time that
# has already passed by the time the request lands.
_START_CUSHION = dt.timedelta(minutes=2)


def create_broadcast(
    *,
    title: str,
    description: str,
    privacy_status: str = "unlisted",
    made_for_kids: bool = False,
    latency: str = "low",
    enable_dvr: bool = True,
) -> Broadcast:
    """`liveBroadcasts.insert` - 50 units.

    See the module docstring for why auto-stop and the monitor stream are
    off; they are not tunable here because getting either wrong turns a
    match into a 60-second clip or a stream nobody ever sees.

    There is no start time to pass. The broadcast starts when the encoder
    connects, which is the only schedule a gym keeps.
    """
    if privacy_status not in VALID_PRIVACY:
        raise RuntimeError(
            f"privacy_status must be one of {VALID_PRIVACY}, got {privacy_status!r}"
        )
    start = dt.datetime.now().astimezone() + _START_CUSHION

    youtube = _build_service()
    body = {
        "snippet": {
            "title": title,
            "description": description,
            "scheduledStartTime": start.isoformat(),
        },
        "status": {
            "privacyStatus": privacy_status,
            # Required field, not an optional nicety.
            "selfDeclaredMadeForKids": made_for_kids,
        },
        "contentDetails": {
            "enableAutoStart": True,
            # Deliberate: a dropout must not end the match.
            "enableAutoStop": False,
            "enableDvr": enable_dvr,
            "recordFromStart": True,
            "latencyPreference": latency,
            # No testing phase - nobody is at a computer during a match.
            "monitorStream": {"enableMonitorStream": False},
        },
    }
    try:
        resp = (
            youtube.liveBroadcasts()
            .insert(part="id,snippet,status,contentDetails", body=body)
            .execute()
        )
        quota.record("liveBroadcasts.insert", note=title[:60])
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc, "creating the broadcast") from exc
    return _to_broadcast(resp)


def bind_broadcast(broadcast_id: str, stream_id: str) -> Broadcast:
    """`liveBroadcasts.bind` - 50 units. Points a broadcast at a stream."""
    youtube = _build_service()
    try:
        resp = (
            youtube.liveBroadcasts()
            .bind(id=broadcast_id, part="id,snippet,status,contentDetails", streamId=stream_id)
            .execute()
        )
        quota.record("liveBroadcasts.bind", note=broadcast_id)
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc, "binding the broadcast to the stream") from exc
    return _to_broadcast(resp)


def delete_broadcast(broadcast_id: str) -> None:
    """`liveBroadcasts.delete` - 50 units.

    Used when a match is cancelled. Leaving a prepared broadcast bound is
    not harmless: the next one to bind takes the stream over, and a stale
    "upcoming" entry sits on the channel looking like a scheduling error.
    """
    youtube = _build_service()
    try:
        youtube.liveBroadcasts().delete(id=broadcast_id).execute()
        quota.record("liveBroadcasts.delete", note=broadcast_id)
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc, "deleting the broadcast") from exc


def get_broadcast(broadcast_id: str) -> Optional[Broadcast]:
    """`liveBroadcasts.list` - 1 unit. None when it no longer exists.

    Distinguishing "deleted on YouTube" from "we never checked" matters
    for the same reason it does for uploads: the UI must not offer to
    reuse a broadcast that is gone.
    """
    youtube = _build_service()
    try:
        resp = (
            youtube.liveBroadcasts()
            .list(part="id,snippet,status,contentDetails", id=broadcast_id)
            .execute()
        )
        quota.record("liveBroadcasts.list", note=broadcast_id)
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc, "reading the broadcast") from exc
    items = resp.get("items", [])
    if not items:
        return None
    return _to_broadcast(items[0])


def set_broadcast_thumbnail(broadcast_id: str, image_path: Path) -> None:
    """Attach a thumbnail to a broadcast (`thumbnails.set`, 50 units).

    A broadcast *is* a video, so this is the same call the render uploads
    use - deliberately duplicated here rather than imported, because the
    upload version raises upload-specific advice ("was it deleted on
    YouTube?") that reads as nonsense for a broadcast that has not aired.
    """
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
            videoId=broadcast_id,
            media_body=MediaFileUpload(str(image_path), mimetype=mimetype),
        ).execute()
        quota.record("thumbnails.set", note=f"broadcast {broadcast_id}")
    except Exception as exc:  # noqa: BLE001
        raise _translate(exc, "setting the broadcast thumbnail") from exc


def _to_broadcast(item: dict) -> Broadcast:
    snippet = item.get("snippet", {}) or {}
    status = item.get("status", {}) or {}
    details = item.get("contentDetails", {}) or {}
    bid = item.get("id", "")
    return Broadcast(
        id=bid,
        title=snippet.get("title", ""),
        description=snippet.get("description", ""),
        privacy_status=status.get("privacyStatus", ""),
        life_cycle_status=status.get("lifeCycleStatus", ""),
        scheduled_start=snippet.get("scheduledStartTime"),
        bound_stream_id=details.get("boundStreamId"),
        watch_url=f"{WATCH_URL}{bid}" if bid else "",
    )
