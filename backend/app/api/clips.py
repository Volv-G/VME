"""Clip endpoints: upload, reorder, delete, auto-cuts."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

from ..library import paths, scanner
from ..library.probe import probe
from ..domain.events.timeline import CutEndEvent, CutStartEvent
from ..domain.match import Clip, Match, new_clip_id
from .helpers import load_match_or_404, save_match, serialize_match
from .schemas import AutoCutsOut, MatchOut, ReorderClipsIn

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/teams/{team}/matches/{match}/clips", tags=["clips"])

# Cut window around each qualifying clip boundary, in seconds. We hide the
# last `AUTO_CUT_PAD_SECONDS` of clip N and the first `AUTO_CUT_PAD_SECONDS`
# of clip N+1 so the join itself disappears into a crossfade.
AUTO_CUT_PAD_SECONDS = 1.0
# Cuts are skipped if either neighbouring clip is shorter than this many
# pad-windows of footage; we always want some visible frames on each side.
AUTO_CUT_MIN_CLIP_SECONDS = 2.0
# Maximum recording-time gap (seconds) between consecutive clips that still
# counts as "continuous recording" and gets bridged with a cut.
AUTO_CUT_MAX_GAP_SECONDS = 1.0
# Default crossfade applied by the generated CutEndEvent.
AUTO_CUT_FADE_FRAMES = 30


@router.post("", response_model=MatchOut)
async def upload_clip(team: str, match: str, file: UploadFile = File(...)) -> MatchOut:
    folder = paths.match_dir(team, match)
    if not folder.exists():
        raise HTTPException(404, "Match folder not found")

    safe_name = Path(file.filename or "upload.mp4").name
    dest = folder / safe_name
    if dest.exists():
        # Stash with numeric suffix.
        stem, suffix = dest.stem, dest.suffix
        n = 1
        while True:
            candidate = folder / f"{stem}_{n}{suffix}"
            if not candidate.exists():
                dest = candidate
                break
            n += 1

    with dest.open("wb") as out:
        shutil.copyfileobj(file.file, out)

    info = probe(dest)
    m = load_match_or_404(team, match)
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
    save_match(team, match, m)
    return serialize_match(team, match, m)


@router.put("", response_model=MatchOut)
def reorder_clips(team: str, match: str, body: ReorderClipsIn) -> MatchOut:
    m = load_match_or_404(team, match)
    try:
        m.reorder_clips(body.clip_ids)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    save_match(team, match, m)
    return serialize_match(team, match, m)


# Must be registered BEFORE `/{clip_id}` so the path "auto-cuts" is not
# captured as a clip id (Starlette returns 405 when the path matches a
# route that declares a different HTTP method).
@router.post("/auto-cuts", response_model=AutoCutsOut)
def auto_cuts(team: str, match: str) -> AutoCutsOut:
    """Insert cuts that bridge back-to-back recordings.

    For each pair of consecutive clips whose recording timestamps are within
    `AUTO_CUT_MAX_GAP_SECONDS`, add a CutStart 1s before the end of the first
    clip and a CutEnd 1s into the second clip. Idempotent: boundaries that
    already have a nearby cut event are skipped.
    """
    m = load_match_or_404(team, match)
    if len(m.clips) < 2:
        return AutoCutsOut(
            match=serialize_match(team, match, m),
            added=0,
            skipped_existing=0,
            skipped_missing_time=0,
            skipped_too_short=0,
            skipped_too_far=0,
        )

    added = 0
    skipped_existing = 0
    skipped_missing_time = 0
    skipped_too_short = 0
    skipped_too_far = 0

    for i in range(len(m.clips) - 1):
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
        if gap < -AUTO_CUT_MAX_GAP_SECONDS or gap > AUTO_CUT_MAX_GAP_SECONDS:
            skipped_too_far += 1
            continue

        cut_start_local = max(0, a.frame_count - pad_a)
        cut_end_local = min(b.frame_count - 1, pad_b)

        # Idempotency: skip if there's already a cut event near these spots.
        if _has_nearby_cut(m, a.id, cut_start_local) or _has_nearby_cut(
            m, b.id, cut_end_local
        ):
            skipped_existing += 1
            continue

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
                fade_frames=AUTO_CUT_FADE_FRAMES,
                frame_shift=-AUTO_CUT_FADE_FRAMES,
            )
        )
        added += 1

    if added:
        save_match(team, match, m)
        logger.info(
            "auto-cuts %s/%s: added=%d skipped_existing=%d too_short=%d too_far=%d missing_time=%d",
            team,
            match,
            added,
            skipped_existing,
            skipped_too_short,
            skipped_too_far,
            skipped_missing_time,
        )

    return AutoCutsOut(
        match=serialize_match(team, match, m),
        added=added,
        skipped_existing=skipped_existing,
        skipped_missing_time=skipped_missing_time,
        skipped_too_short=skipped_too_short,
        skipped_too_far=skipped_too_far,
    )


@router.delete("/{clip_id}", response_model=MatchOut)
def delete_clip(team: str, match: str, clip_id: str) -> MatchOut:
    m = load_match_or_404(team, match)
    clip = m.get_clip(clip_id)
    if clip is None:
        raise HTTPException(404, "Clip not found")
    m.remove_clip(clip_id)
    file_path = paths.match_dir(team, match) / clip.filename
    try:
        file_path.unlink(missing_ok=True)
    except OSError:
        pass
    save_match(team, match, m)
    return serialize_match(team, match, m)


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
