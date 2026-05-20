"""Clip endpoints: upload, reorder, delete, auto-cuts.

Upload flavors
--------------
  1. `POST /clips/uploads` + `PUT /clips/uploads/{sid}/chunk` +
     `POST /clips/uploads/{sid}/finalize` - resumable chunked upload.
     Each PUT is a small body (default 32 MiB) so no individual request
     hits the IIS/ARR ~2 GiB single-request barrier. Default path for
     browser uploads.
  2. `POST /clips/import-path` - server-side ingest from a local file
     or folder path. Skips HTTP entirely; useful when the browser and
     the server are on the same machine and the source video already
     lives somewhere accessible to the service user.

Both flavors converge on `_ingest_file_into_match()` once the file
sits in the match folder. That keeps fps-normalization, probing, and
clip-id assignment in one place.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Optional

from fastapi import APIRouter, HTTPException, Request

from ..library import paths, scanner
from ..library.probe import probe
from ..library.transcode import transcode_to_fps
from ..domain.events.lifecycle import (
    GameEndEvent,
    GameStartEvent,
    SetEndEvent,
)
from ..domain.events.timeline import CutEndEvent, CutStartEvent
from ..domain.match import Clip, Match, new_clip_id
from .helpers import load_match_or_404, save_match, serialize_match
from .schemas import (
    AutoCutsIn,
    AutoCutsOut,
    ImportPathIn,
    ImportPathOut,
    MatchOut,
    ReorderClipsIn,
    UploadSessionStartIn,
    UploadSessionOut,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/teams/{team}/tournaments/{tournament}/dates/{date}/matches/{match}/clips",
    tags=["clips"],
)

# Cut window around each qualifying clip boundary, in seconds. We hide the
# last `AUTO_CUT_PAD_SECONDS` of clip N and the first `AUTO_CUT_PAD_SECONDS`
# of clip N+1 so the join itself disappears into a crossfade.
AUTO_CUT_PAD_SECONDS = 1.0
# Cuts are skipped if either neighbouring clip is shorter than this many
# pad-windows of footage; we always want some visible frames on each side.
AUTO_CUT_MIN_CLIP_SECONDS = 2.0
# Minimum recording-time gap (seconds) between consecutive clips that is
# worth bridging with a crossfade. Pairs with smaller gaps are treated as
# "continuous recording" (camera was rolling, file just split at 4 GB / 30
# min) - a hard cut between them is already invisible, so we leave them
# alone. Adding a crossfade there would only waste 2 s of real footage.
AUTO_CUT_MIN_GAP_SECONDS = 1.0
# Gap threshold (seconds) above which a pair is treated as an inter-set
# break rather than a within-set stop/restart. Auto-cuts inserts a SetEnd
# event (fade-to-black, score reset) at clip A's last frame instead of a
# crossfade. 180 s comfortably covers timeouts and short breaks but is
# short enough to catch any real set break.
AUTO_SET_END_GAP_SECONDS = 180.0
# Crossfade duration applied at each generated cut. Equal to the pad so
# the entire removed window is covered by the blend - the user sees a
# smooth fade across the join with no pure-color hold. Converted to frames
# per-clip via that clip's fps; a hardcoded frame count would silently
# halve at 60 fps and double at 15 fps.
AUTO_CUT_FADE_SECONDS = AUTO_CUT_PAD_SECONDS


# --- Shared ingest helpers ----------------------------------------------------
#
# All three upload flavors (legacy single-request, chunked, path-import)
# converge on `_ingest_file_into_match()` once the source bytes are
# sitting in the match folder. Keeping the probe / transcode / clip-id /
# fps-sync logic in one place means a bugfix to e.g. transcode error
# handling doesn't have to be replicated three times.

_VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm"}


def _resolve_match_folder(
    team: str, tournament: str, date: str, match: str
) -> Path:
    folder = paths.match_dir(team, tournament, date, match)
    if not folder.exists():
        raise HTTPException(404, "Match folder not found")
    return folder


def _next_available_name(folder: Path, basename: str) -> Path:
    """Resolve a non-colliding destination path inside `folder`.

    Mirrors the pre-existing collision rule: append `_1`, `_2`, ... to
    the stem until the name is free.
    """
    dest = folder / Path(basename).name  # strip any leading path components
    if not dest.exists():
        return dest
    stem, suffix = dest.stem, dest.suffix
    n = 1
    while True:
        candidate = folder / f"{stem}_{n}{suffix}"
        if not candidate.exists():
            return candidate
        n += 1


def _ingest_file_into_match(
    team: str,
    tournament: str,
    date: str,
    match: str,
    dest: Path,
    *,
    m: Optional[Match] = None,
    save: bool = True,
) -> Match:
    """Probe `dest`, transcode-if-needed, add to `m.clips`, save.

    `m` is loaded from disk if not provided. `save=False` lets callers
    batch many files into a single save (path-import folder mode).
    Returns the (possibly newly loaded) Match.

    Raises HTTPException on transcode failure, after removing `dest` so
    a retry isn't blocked by the partial file.
    """
    info = probe(dest)
    if m is None:
        m = load_match_or_404(team, tournament, date, match)

    # FPS normalization rule: the first clip's playback rate becomes the
    # project FPS (sync_match_fps below). Every subsequent clip whose
    # native rate differs is transcoded in place to the project FPS so
    # all the frame-based math stays consistent.
    if (
        m.clips
        and info is not None
        and info.fps > 0
        and abs(info.fps - m.fps) > 0.01
    ):
        source_fps = info.fps
        logger.info(
            "clip %s native fps %.3f != project fps %.3f; transcoding",
            dest.name,
            source_fps,
            m.fps,
        )
        if not transcode_to_fps(dest, m.fps):
            dest.unlink(missing_ok=True)
            raise HTTPException(
                500,
                f"Failed to transcode {dest.name} from {source_fps:.2f} "
                f"fps to project rate {m.fps:.2f} fps. Source kept on disk "
                f"was removed; re-upload and try again.",
            )
        # Re-probe after transcode so the recorded clip metadata reflects
        # the new fps / frame_count.
        info = probe(dest)

    if not any(c.filename == dest.name for c in m.clips):
        m.add_clip(
            Clip(
                id=new_clip_id(),
                filename=dest.name,
                frame_count=info.frame_count if info else 0,
                fps=info.fps if info else m.fps or 30.0,
                width=info.width if info else 0,
                height=info.height if info else 0,
                start_recording_time=info.start_recording_time if info else None,
            )
        )
    scanner.sync_match_fps(m)
    if save:
        save_match(team, tournament, date, match, m)
    return m


# --- Chunked / resumable upload -----------------------------------------------
#
# Protocol:
#   POST   /clips/uploads          {filename, total_size}  -> {session_id, received: 0}
#   PUT    /clips/uploads/{sid}/chunk?offset=<bytes>       -> {received: <new>}
#                                  body = raw octet-stream (one chunk)
#   POST   /clips/uploads/{sid}/finalize                   -> MatchOut
#   DELETE /clips/uploads/{sid}                            -> {cancelled: true}
#
# Sessions live in-memory (lost across service restart - that's fine for
# v1). The corresponding `.upload-{sid}.<name>.part` files persist on
# disk; orphans are cleaned up the next time `_purge_stale_sessions()`
# runs (called from session create, so each new upload tidies the slate).
#
# Each PUT carries one chunk. The default client chunk size is 32 MiB,
# well under the IIS 2 GiB barrier. Chunks must arrive in order from the
# current `received` offset; the offset query parameter is checked
# against the on-disk file size so a client retry that resends the same
# chunk is rejected (avoids duplicating bytes).

_UPLOAD_PART_PREFIX = ".upload-"
_SESSION_TTL_SECONDS = 6 * 3600  # 6 h - long enough for any realistic upload


@dataclass
class _UploadSession:
    session_id: str
    team: str
    tournament: str
    date: str
    match: str
    filename: str  # eventual destination basename (collision-resolved)
    total_size: int
    part_path: Path  # absolute path to the .part file
    received: int = 0
    started_at: float = field(default_factory=time.time)
    lock: Lock = field(default_factory=Lock)


_sessions: dict[str, _UploadSession] = {}
_sessions_lock = Lock()


def _purge_stale_sessions() -> None:
    """Drop sessions older than TTL and delete their .part files.

    Called opportunistically from session create. Cheap (linear in the
    session count, which is bounded by concurrent uploads).
    """
    cutoff = time.time() - _SESSION_TTL_SECONDS
    with _sessions_lock:
        stale = [sid for sid, s in _sessions.items() if s.started_at < cutoff]
        for sid in stale:
            sess = _sessions.pop(sid)
            sess.part_path.unlink(missing_ok=True)


@router.post("/uploads", response_model=UploadSessionOut)
def start_upload_session(
    team: str,
    tournament: str,
    date: str,
    match: str,
    body: UploadSessionStartIn,
) -> UploadSessionOut:
    """Create a chunked-upload session.

    Reserves the destination filename now (collision-resolved) so a
    second concurrent session for the same source can't race onto the
    same .part path.
    """
    _purge_stale_sessions()
    folder = _resolve_match_folder(team, tournament, date, match)

    if body.total_size <= 0:
        raise HTTPException(400, "total_size must be positive")
    safe_basename = Path(body.filename or "upload.mp4").name
    if not safe_basename:
        raise HTTPException(400, "filename required")

    # Resolve collision NOW so the final-name slot is taken even before
    # the first byte arrives. Concurrent sessions for the same source
    # name get distinct destinations (foo.mp4, foo_1.mp4, ...).
    dest = _next_available_name(folder, safe_basename)

    session_id = uuid.uuid4().hex
    part_path = folder / f"{_UPLOAD_PART_PREFIX}{session_id}.{dest.name}.part"
    # Touch the part file so reads of its size succeed on the first PUT.
    part_path.touch()

    sess = _UploadSession(
        session_id=session_id,
        team=team,
        tournament=tournament,
        date=date,
        match=match,
        filename=dest.name,
        total_size=body.total_size,
        part_path=part_path,
    )
    with _sessions_lock:
        _sessions[session_id] = sess
    return UploadSessionOut(
        session_id=session_id,
        filename=dest.name,
        total_size=body.total_size,
        received=0,
    )


def _get_session(sid: str) -> _UploadSession:
    with _sessions_lock:
        sess = _sessions.get(sid)
    if sess is None:
        raise HTTPException(404, "Upload session not found or expired")
    return sess


@router.put("/uploads/{session_id}/chunk", response_model=UploadSessionOut)
async def append_upload_chunk(
    team: str,
    tournament: str,
    date: str,
    match: str,
    session_id: str,
    offset: int,
    request: Request,
) -> UploadSessionOut:
    """Append a chunk at `offset`. Body is raw octet-stream bytes.

    `offset` MUST equal the current `received` count - this rejects
    both duplicate retries (offset behind) and skipped chunks (offset
    ahead). A client that wants to recover should query the session,
    discover the true `received`, and resume from there. Per-session
    lock serializes concurrent chunks for the same session.
    """
    sess = _get_session(session_id)
    if (sess.team, sess.tournament, sess.date, sess.match) != (
        team, tournament, date, match,
    ):
        # The session belongs to a different match. Refuse rather than
        # silently write to the wrong folder.
        raise HTTPException(400, "Session does not belong to this match")

    with sess.lock:
        if offset != sess.received:
            raise HTTPException(
                409,
                f"Offset mismatch: expected {sess.received}, got {offset}",
            )
        # Stream the request body to the part file. Iterating the raw
        # stream avoids buffering the full chunk in memory; the chunk
        # may be tens of MiB.
        bytes_written = 0
        with sess.part_path.open("ab") as f:
            async for piece in request.stream():
                if not piece:
                    continue
                f.write(piece)
                bytes_written += len(piece)
        sess.received += bytes_written
        if sess.received > sess.total_size:
            # Client lied about total_size. Trim back so finalize won't
            # silently accept a too-large file.
            sess.part_path.unlink(missing_ok=True)
            with _sessions_lock:
                _sessions.pop(session_id, None)
            raise HTTPException(
                400,
                f"Received {sess.received} bytes, exceeds declared total_size "
                f"{sess.total_size}; session aborted",
            )

    return UploadSessionOut(
        session_id=session_id,
        filename=sess.filename,
        total_size=sess.total_size,
        received=sess.received,
    )


@router.get("/uploads/{session_id}", response_model=UploadSessionOut)
def get_upload_session(
    team: str,
    tournament: str,
    date: str,
    match: str,
    session_id: str,
) -> UploadSessionOut:
    """Inspect session state. Used by clients to resume after a network
    blip (find out how many bytes the server already has)."""
    sess = _get_session(session_id)
    return UploadSessionOut(
        session_id=session_id,
        filename=sess.filename,
        total_size=sess.total_size,
        received=sess.received,
    )


@router.post("/uploads/{session_id}/finalize", response_model=MatchOut)
def finalize_upload(
    team: str,
    tournament: str,
    date: str,
    match: str,
    session_id: str,
) -> MatchOut:
    """Promote the .part file to its final name and ingest into the match."""
    sess = _get_session(session_id)
    with sess.lock:
        if sess.received != sess.total_size:
            raise HTTPException(
                409,
                f"Cannot finalize: received {sess.received} of {sess.total_size} bytes",
            )
        folder = _resolve_match_folder(team, tournament, date, match)
        # The destination slot was reserved at session start, but check
        # again in case another flow (manual file drop + rescan, second
        # session) grabbed it in the meantime.
        final_path = folder / sess.filename
        if final_path.exists():
            final_path = _next_available_name(folder, sess.filename)
            sess.filename = final_path.name
        os.replace(sess.part_path, final_path)
        with _sessions_lock:
            _sessions.pop(session_id, None)
    m = _ingest_file_into_match(team, tournament, date, match, final_path)
    return serialize_match(team, tournament, date, match, m)


@router.delete("/uploads/{session_id}")
def cancel_upload(
    team: str,
    tournament: str,
    date: str,
    match: str,
    session_id: str,
) -> dict[str, bool]:
    """Cancel an in-progress upload. Removes the .part file."""
    with _sessions_lock:
        sess = _sessions.pop(session_id, None)
    if sess is not None:
        sess.part_path.unlink(missing_ok=True)
    return {"cancelled": True}


# --- Server-side path import --------------------------------------------------


@router.post("/import-path", response_model=ImportPathOut)
def import_from_path(
    team: str,
    tournament: str,
    date: str,
    match: str,
    body: ImportPathIn,
) -> ImportPathOut:
    """Ingest one or more video files from a local server-side path.

    Bypasses HTTP transfer entirely - the path is read directly by the
    service process from the local filesystem. Useful when the browser
    and server are on the same machine and the source clip already
    sits somewhere accessible to the service user.

    `source_path` may point at:
      - a single video file: imported as one clip.
      - a directory: all top-level video files in it are imported, in
        sorted filename order.

    `mode` selects copy (default, non-destructive) or move (instant
    rename when source and dest are on the same drive, otherwise a
    copy+delete). Move is preferable for large files to avoid doubling
    disk usage during ingest.
    """
    src = Path(body.source_path).expanduser()
    if not src.is_absolute():
        raise HTTPException(400, "source_path must be absolute")
    if not src.exists():
        raise HTTPException(404, f"Source not found: {src}")

    if src.is_dir():
        files = sorted(
            p for p in src.iterdir()
            if p.is_file() and p.suffix.lower() in _VIDEO_EXTENSIONS
        )
        if not files:
            raise HTTPException(
                400, f"No recognized video files in directory: {src}"
            )
    else:
        if src.suffix.lower() not in _VIDEO_EXTENSIONS:
            raise HTTPException(
                400, f"Not a recognized video file extension: {src.suffix}"
            )
        files = [src]

    folder = _resolve_match_folder(team, tournament, date, match)
    move = body.mode == "move"

    # Load once, mutate across all files, save once at the end. Avoids
    # N round-trips through the on-disk match.json.
    m = load_match_or_404(team, tournament, date, match)
    imported_names: list[str] = []
    for src_file in files:
        dest = _next_available_name(folder, src_file.name)
        try:
            if move:
                # `os.replace` does an atomic rename when source and
                # dest are on the same volume; falls back to copy+
                # remove on cross-volume moves (shutil.move handles
                # both).
                shutil.move(str(src_file), str(dest))
            else:
                shutil.copy2(src_file, dest)
        except OSError as exc:
            raise HTTPException(
                500, f"Failed to {'move' if move else 'copy'} {src_file}: {exc}"
            ) from exc
        _ingest_file_into_match(
            team, tournament, date, match, dest, m=m, save=False
        )
        imported_names.append(dest.name)

    save_match(team, tournament, date, match, m)
    return ImportPathOut(
        imported=imported_names,
        match=serialize_match(team, tournament, date, match, m),
    )


@router.put("", response_model=MatchOut)
def reorder_clips(
    team: str,
    tournament: str,
    date: str,
    match: str,
    body: ReorderClipsIn,
) -> MatchOut:
    m = load_match_or_404(team, tournament, date, match)
    try:
        m.reorder_clips(body.clip_ids)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    save_match(team, tournament, date, match, m)
    return serialize_match(team, tournament, date, match, m)


# Must be registered BEFORE `/{clip_id}` so the path "auto-cuts" is not
# captured as a clip id (Starlette returns 405 when the path matches a
# route that declares a different HTTP method).
@router.post("/auto-cuts", response_model=AutoCutsOut)
def auto_cuts(
    team: str,
    tournament: str,
    date: str,
    match: str,
    body: AutoCutsIn | None = None,
) -> AutoCutsOut:
    """Insert crossfades between clips separated by a real recording gap.

    Volleyball cameras typically auto-split a single rolling recording into
    several files (4 GB / 30 min limit). Joining those continuous pieces
    needs no help: a hard cut between two frames that look identical is
    invisible. The interesting case is the OPPOSITE - when the operator
    stopped and restarted between rallies, the join becomes a visible
    scene-jump that benefits from a crossfade.

    For each pair of consecutive clips, classify by recording-time gap:
      - gap < `AUTO_CUT_MIN_GAP_SECONDS`: continuous recording, leave alone.
      - gap between min and `AUTO_SET_END_GAP_SECONDS`: drop the trailing
        1 s of clip N and the leading 1 s of clip N+1, place a crossfade
        across the resulting join.
      - gap > `AUTO_SET_END_GAP_SECONDS`: treat as an inter-set break -
        insert a `SetEndEvent` at clip A's last frame (fade-to-black +
        score reset on the next set).

    Idempotent: boundaries that already have a nearby cut or set-end event
    are skipped.
    """
    m = load_match_or_404(team, tournament, date, match)
    has_intro = bool(body and body.has_intro_clip)

    added = 0
    added_set_ends = 0
    added_game_start = 0
    added_game_end = 0
    skipped_existing = 0
    skipped_missing_time = 0
    skipped_too_short = 0
    skipped_too_close = 0

    # ---- GameStart -------------------------------------------------------
    # Place a GameStart at the start of gameplay. With an intro clip, the
    # intro is clip 0 and the game begins at clip 1; without one, clip 0
    # is gameplay from frame 0. In both cases the lifecycle event's own
    # fade-from-black gives a clean transition into the scoreboard view.
    if m.clips:
        if has_intro and len(m.clips) >= 2:
            gs_clip = m.clips[1]
        else:
            gs_clip = m.clips[0]
        if not _has_nearby_lifecycle(m, gs_clip.id, 0, "game_start"):
            fade = max(1, round(gs_clip.fps * AUTO_CUT_FADE_SECONDS))
            m.add_event(
                GameStartEvent(
                    id=0,
                    clip_id=gs_clip.id,
                    local_frame=0,
                    fade_frames=fade,
                )
            )
            added_game_start = 1

    # ---- GameEnd at end of last clip ------------------------------------
    if m.clips:
        last = m.clips[-1]
        last_local = last.frame_count - 1
        if not _has_nearby_lifecycle(m, last.id, last_local, "game_end"):
            fade = max(1, round(last.fps * AUTO_CUT_FADE_SECONDS))
            m.add_event(
                GameEndEvent(
                    id=0,
                    clip_id=last.id,
                    local_frame=last_local,
                    fade_frames=fade,
                )
            )
            added_game_end = 1

    if len(m.clips) < 2:
        if added_game_start or added_game_end:
            save_match(team, tournament, date, match, m)
        return AutoCutsOut(
            match=serialize_match(team, tournament, date, match, m),
            added=0,
            added_set_ends=0,
            added_game_start=added_game_start,
            added_game_end=added_game_end,
            skipped_existing=0,
            skipped_missing_time=0,
            skipped_too_short=0,
            skipped_too_close=0,
        )

    # With an intro clip, skip the pair (0, 1): the GameStart fade-in we
    # just inserted is the transition - adding a crossfade there too
    # would double-blend the same join.
    first_pair = 1 if has_intro else 0
    for i in range(first_pair, len(m.clips) - 1):
        a, b = m.clips[i], m.clips[i + 1]
        pad_a = round(a.fps * AUTO_CUT_PAD_SECONDS)
        pad_b = round(b.fps * AUTO_CUT_PAD_SECONDS)
        min_a_frames = int(a.fps * AUTO_CUT_MIN_CLIP_SECONDS)
        min_b_frames = int(b.fps * AUTO_CUT_MIN_CLIP_SECONDS)
        if a.frame_count < min_a_frames or b.frame_count < min_b_frames:
            skipped_too_short += 1
            continue

        if a.end_recording_time is None or b.start_recording_time is None:
            skipped_missing_time += 1
            continue

        gap = b.start_recording_time - a.end_recording_time
        # Continuous recording (or near-overlap): hard cut is already
        # invisible. Skip - adding a crossfade here would only waste 2 s of
        # real footage.
        if gap < AUTO_CUT_MIN_GAP_SECONDS:
            skipped_too_close += 1
            continue

        # Inter-set break: insert a SetEnd at A's last frame instead of a
        # crossfade. The lifecycle event's own fade_frames default produces
        # a fade-to-black; SetEnd's score effect resets the scoreboard for
        # the next set.
        if gap > AUTO_SET_END_GAP_SECONDS:
            set_end_local = a.frame_count - 1
            if _has_nearby_set_end(m, a.id, set_end_local):
                skipped_existing += 1
                continue
            m.add_event(
                SetEndEvent(
                    id=0,  # add_event assigns a real id
                    clip_id=a.id,
                    local_frame=set_end_local,
                )
            )
            added_set_ends += 1
            continue

        cut_start_local = max(0, a.frame_count - pad_a)
        cut_end_local = min(b.frame_count - 1, pad_b)

        # Idempotency: skip if there's already a cut event near these spots.
        if _has_nearby_cut(m, a.id, cut_start_local) or _has_nearby_cut(
            m, b.id, cut_end_local
        ):
            skipped_existing += 1
            continue

        # Fade is anchored to clip B (where the CutEnd lives), so compute
        # it from B's fps. frame_shift = -fade_frames produces a pure
        # crossfade with no color hold.
        fade_frames = round(b.fps * AUTO_CUT_FADE_SECONDS)
        m.add_event(
            CutStartEvent(
                id=0,  # add_event assigns a real id
                clip_id=a.id,
                local_frame=cut_start_local,
            )
        )
        m.add_event(
            CutEndEvent(
                id=0,
                clip_id=b.id,
                local_frame=cut_end_local,
                fade_frames=fade_frames,
                frame_shift=-fade_frames,
            )
        )
        added += 1

    if added or added_set_ends or added_game_start or added_game_end:
        save_match(team, tournament, date, match, m)
        logger.info(
            "auto-cuts %s/%s/%s/%s: cuts=%d set_ends=%d game_start=%d game_end=%d skipped_existing=%d too_short=%d too_close=%d missing_time=%d",
            team,
            tournament,
            date,
            match,
            added,
            added_set_ends,
            added_game_start,
            added_game_end,
            skipped_existing,
            skipped_too_short,
            skipped_too_close,
            skipped_missing_time,
        )

    return AutoCutsOut(
        match=serialize_match(team, tournament, date, match, m),
        added=added,
        added_set_ends=added_set_ends,
        added_game_start=added_game_start,
        added_game_end=added_game_end,
        skipped_existing=skipped_existing,
        skipped_missing_time=skipped_missing_time,
        skipped_too_short=skipped_too_short,
        skipped_too_close=skipped_too_close,
    )


@router.delete("/{clip_id}", response_model=MatchOut)
def delete_clip(
    team: str, tournament: str, date: str, match: str, clip_id: str
) -> MatchOut:
    m = load_match_or_404(team, tournament, date, match)
    clip = m.get_clip(clip_id)
    if clip is None:
        raise HTTPException(404, "Clip not found")
    m.remove_clip(clip_id)
    file_path = paths.match_dir(team, tournament, date, match) / clip.filename
    try:
        file_path.unlink(missing_ok=True)
    except OSError:
        pass
    save_match(team, tournament, date, match, m)
    return serialize_match(team, tournament, date, match, m)


def _has_nearby_cut(m: Match, clip_id: str, local_frame: int, tolerance: int = 30) -> bool:
    """True if a cut_start or cut_end already sits within `tolerance` frames."""
    for e in m.events:
        if not isinstance(e, (CutStartEvent, CutEndEvent)):
            continue
        if e.clip_id != clip_id:
            continue
        if abs(e.local_frame - local_frame) <= tolerance:
            return True
    return False


def _has_nearby_set_end(
    m: Match, clip_id: str, local_frame: int, tolerance: int = 30
) -> bool:
    """True if a SetEnd already sits within `tolerance` frames of this spot."""
    for e in m.events:
        if not isinstance(e, SetEndEvent):
            continue
        if e.clip_id != clip_id:
            continue
        if abs(e.local_frame - local_frame) <= tolerance:
            return True
    return False


def _has_nearby_lifecycle(
    m: Match,
    clip_id: str,
    local_frame: int,
    type_name: str,
    tolerance: int = 30,
) -> bool:
    """True if a GameStart / GameEnd of `type_name` already sits within
    `tolerance` frames of this spot. Used for idempotency: re-running
    auto-cuts should not stack a fresh GameStart on top of an existing
    one. Matching by `type_name` (not class) is fine because both event
    classes set their `type_name` ClassVar to a unique string and either
    one would be wrong to duplicate.
    """
    for e in m.events:
        if e.type_name != type_name:
            continue
        if e.clip_id != clip_id:
            continue
        if abs(e.local_frame - local_frame) <= tolerance:
            return True
    return False
