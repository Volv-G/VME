"""Execute a single render job synchronously.

Called by the dispatcher thread (one job at a time). The previous
`kick_off_render(...)` helper that spawned its own thread is gone -
concurrency is now owned by the dispatcher, not individual job calls.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..library import paths, scanner
from ..render.batch import (
    BatchClip,
    focused_from_match,
    highlights_from_match,
)
from ..render.overlays.scoreboard import TeamBranding
from ..render.renderer import MatchRenderer, RenderCancelled, RenderProgress
from .manager import JOBS, RenderJob

logger = logging.getLogger(__name__)


# Default half-window used for previews when the client doesn't override it.
DEFAULT_PREVIEW_SECONDS_AROUND = 30.0


def run_render(job: RenderJob) -> None:
    """Run `job` synchronously in the current thread.

    All status transitions go through `JOBS.update(...)`; the dispatcher
    is responsible for catching exceptions and translating them into a
    failed status. We do still catch `RenderCancelled` here because we
    need to clean up the partial output file before re-raising.

    Dispatch on `job.kind`:
      - "full" / "preview" -> single-output path via MatchRenderer.
      - "highlights" / "focused_highlights" -> batch path producing one
        mp4 per detected span (see `app/render/batch.py`).
    """
    JOBS.mark_started(job.id)

    cancel_event = job.cancel_event

    def cancel_check() -> bool:
        return cancel_event.is_set()

    m = scanner.load_or_create_match(job.team, job.tournament, job.date, job.match)
    if not m.clips:
        raise RuntimeError("Match has no clips to render")

    if job.kind in ("highlights", "focused_highlights"):
        _run_batch(job, m, cancel_check)
        return

    home_roster = scanner.load_team_roster(job.team)
    home = TeamBranding(
        name=home_roster.team_name or job.team,
        color=home_roster.team_color or "#2d8a4e",
    )
    away = TeamBranding(
        name=m.opponent_roster.team_name or m.opponent or "Away",
        color=m.opponent_roster.team_color or "#8a2d2d",
    )

    # Resolve a source frame range when a playhead_frame was supplied.
    source_frame_range: Optional[tuple[int, int]] = None
    if job.playhead_frame is not None:
        half = float(
            job.seconds_around
            if job.seconds_around is not None
            else DEFAULT_PREVIEW_SECONDS_AROUND
        )
        half_frames = int(round(half * (m.fps or 30.0)))
        total_src = m.total_frames()
        start = max(0, job.playhead_frame - half_frames)
        end = min(total_src, job.playhead_frame + half_frames)
        if end <= start:
            raise RuntimeError(
                "Preview window is empty (check playhead_frame / seconds_around)"
            )
        source_frame_range = (start, end)
        logger.info(
            "preview render: playhead=%d half=%.1fs -> source frames [%d, %d)",
            job.playhead_frame,
            half,
            start,
            end,
        )

    renders = paths.renders_dir(job.team, job.tournament, job.date, job.match)
    renders.mkdir(parents=True, exist_ok=True)

    label_segment = (job.label or "render").lower().replace(" ", "_")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_filename = f"{label_segment}_{timestamp}.mp4"
    output_path = renders / output_filename

    media_dir = paths.match_dir(job.team, job.tournament, job.date, job.match)

    last_pct = [0.0]
    last_emit = [time.time()]

    def on_progress(p: RenderProgress) -> None:
        now = time.time()
        if p.percent - last_pct[0] >= 0.5 or now - last_emit[0] > 0.4:
            JOBS.update(
                job.id,
                percent=p.percent,
                phase=p.phase,
                message=f"{p.frames_done}/{p.frames_total} frames",
            )
            last_pct[0] = p.percent
            last_emit[0] = now

    try:
        with MatchRenderer(
            m,
            media_dir,
            home=home,
            away=away,
            home_roster=home_roster,
            away_roster=m.opponent_roster,
        ) as r:
            r.render(
                output_path,
                progress=on_progress,
                source_frame_range=source_frame_range,
                cancel_check=cancel_check,
            )
    except RenderCancelled:
        # Clean up the partial output before letting the dispatcher mark
        # the job cancelled.
        try:
            output_path.unlink(missing_ok=True)
        except Exception:
            logger.warning(
                "failed to remove partial render output: %s", output_path
            )
        raise

    JOBS.mark_done(job.id, output_filename)


# ---------------------------------------------------------------------------
# Batch path (highlights, focused_highlights)
# ---------------------------------------------------------------------------


def _run_batch(
    job: RenderJob,
    m,  # Match
    cancel_check,  # Callable[[], bool]
) -> None:
    """Render one mp4 per detected span; report aggregate progress.

    Output goes under the match's renders folder in a `highlights/` or
    `focused/` subtree, organized by team and player. The job's
    `output_filename` is set to the first produced file so the UI's
    download link still works; the full list is discoverable via the
    recursive `/renders` listing.
    """
    home_roster = scanner.load_team_roster(job.team)
    home = TeamBranding(
        name=home_roster.team_name or job.team,
        color=home_roster.team_color or "#2d8a4e",
    )
    away = TeamBranding(
        name=m.opponent_roster.team_name or m.opponent or "Away",
        color=m.opponent_roster.team_color or "#8a2d2d",
    )

    # Detect spans up front so we know `total` for progress reporting and
    # can fail fast if there's nothing to render.
    if job.kind == "highlights":
        clips = highlights_from_match(
            m, home_roster, home_roster.team_name or job.team
        )
        what = "highlight"
    elif job.kind == "focused_highlights":
        clips = focused_from_match(
            m, home_roster, home_roster.team_name or job.team
        )
        what = "focused clip"
    else:
        raise RuntimeError(f"Unknown batch kind: {job.kind!r}")

    if not clips:
        JOBS.update(
            job.id,
            message=f"No {what}s found in this match",
        )
        # Treat "nothing to do" as success - the job ran to completion,
        # there was just no output. Avoids a confusing FAILED row when
        # the user runs highlights on an unannotated match.
        JOBS.mark_done(job.id, "")
        return

    media_dir = paths.match_dir(job.team, job.tournament, job.date, job.match)
    renders_root = paths.renders_dir(
        job.team, job.tournament, job.date, job.match
    )
    renders_root.mkdir(parents=True, exist_ok=True)

    # Aggregate progress: total output frames across all sub-clips, plus
    # a running tally of frames committed by sub-clips that have already
    # finished. The renderer's per-frame progress callback adds to this
    # so the bar moves smoothly across the whole batch.
    total_frames = sum(max(0, c.end_frame - c.start_frame) for c in clips)
    done_before_current = [0]
    current_total = [0]
    last_pct = [0.0]
    last_emit = [time.time()]

    def on_progress(p: RenderProgress) -> None:
        # `p.frames_done` is local to the current sub-clip. We re-base it
        # onto the batch total. Throttle SSE updates the same way the
        # single-render path does to avoid spamming the manager.
        now = time.time()
        overall = (done_before_current[0] + p.frames_done) / max(1, total_frames) * 100.0
        if overall - last_pct[0] >= 0.5 or now - last_emit[0] > 0.4:
            JOBS.update(
                job.id,
                percent=overall,
                phase=p.phase,
                message=(
                    f"{done_before_current[0] + p.frames_done}"
                    f"/{total_frames} frames "
                    f"({current_total[0]} clip(s) done of {len(clips)})"
                ),
            )
            last_pct[0] = overall
            last_emit[0] = now

    first_output: Optional[str] = None

    with MatchRenderer(
        m,
        media_dir,
        home=home,
        away=away,
        home_roster=home_roster,
        away_roster=m.opponent_roster,
    ) as r:
        for i, bc in enumerate(clips, start=1):
            if cancel_check():
                raise RenderCancelled("Render cancelled by user")

            output_path = renders_root / bc.relative_path
            output_path.parent.mkdir(parents=True, exist_ok=True)
            # Brief status update so the UI shows which clip is starting
            # even before the first per-frame progress tick arrives.
            JOBS.update(
                job.id,
                message=f"[{i}/{len(clips)}] {bc.label}",
            )
            try:
                r.render(
                    output_path,
                    progress=on_progress,
                    source_frame_range=(bc.start_frame, bc.end_frame),
                    cancel_check=cancel_check,
                )
            except RenderCancelled:
                # Drop the partial sub-clip before re-raising. Earlier
                # completed clips stay on disk - they're independently
                # useful and re-running the batch is idempotent for
                # those (same filenames).
                try:
                    output_path.unlink(missing_ok=True)
                except Exception:
                    logger.warning(
                        "failed to remove partial batch output: %s",
                        output_path,
                    )
                raise

            done_before_current[0] += max(0, bc.end_frame - bc.start_frame)
            current_total[0] = i
            if first_output is None:
                first_output = bc.relative_path

    # `output_filename` is used by the UI as a download link target; with
    # nested subfolders the relative_path is the right thing to record.
    JOBS.mark_done(job.id, first_output or "")


def kick_off_immediate(job: RenderJob) -> None:
    """Run `job` in a fresh daemon thread, independent of the dispatcher.

    Used for preview renders: the user wants instant feedback, so we
    don't make them wait for the queue to be active or for an in-flight
    full render to finish. The job is still tracked in the registry,
    persisted, and emits SSE progress like any other.

    Pre-conditions: `job.immediate` must be True. The dispatcher excludes
    immediate jobs from `next_pending()`, so there's no risk of the
    same job running twice.
    """
    if not job.immediate:
        raise ValueError(
            "kick_off_immediate called for a non-immediate job"
        )

    def _run() -> None:
        try:
            run_render(job)
        except RenderCancelled:
            JOBS.mark_cancelled(job.id)
        except Exception as exc:
            logger.exception("immediate render %s failed: %s", job.id, exc)
            JOBS.mark_failed(job.id, str(exc))

    threading.Thread(
        target=_run, name=f"render-immediate-{job.id}", daemon=True
    ).start()
