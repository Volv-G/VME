"""Run a render job: invoke the renderer in a worker thread, report progress."""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..library import paths, scanner
from ..render.overlays.scoreboard import TeamBranding
from ..render.renderer import MatchRenderer, RenderCancelled, RenderProgress
from .manager import JOBS, JobStatus

logger = logging.getLogger(__name__)


def kick_off_render(
    job_id: str,
    team: str,
    match: str,
    label: str = "",
    *,
    playhead_frame: Optional[int] = None,
    seconds_around: Optional[float] = None,
) -> None:
    """Spawn a daemon thread to perform the render."""

    def run() -> None:
        try:
            _run_render(
                job_id, team, match, label,
                playhead_frame=playhead_frame,
                seconds_around=seconds_around,
            )
        except RenderCancelled:
            # _run_render already handled cleanup + JOBS.mark_cancelled.
            logger.info("render %s cancelled", job_id)
        except Exception as exc:
            logger.exception("render job failed: %s", exc)
            JOBS.mark_failed(job_id, str(exc))

    threading.Thread(target=run, daemon=True, name=f"render-{job_id}").start()


# Default half-window used for previews when the client doesn't override it.
DEFAULT_PREVIEW_SECONDS_AROUND = 30.0


def _run_render(
    job_id: str,
    team: str,
    match: str,
    label: str,
    *,
    playhead_frame: Optional[int] = None,
    seconds_around: Optional[float] = None,
) -> None:
    JOBS.mark_started(job_id)

    job = JOBS.get(job_id)
    cancel_event = job.cancel_event if job is not None else None

    def cancel_check() -> bool:
        return cancel_event is not None and cancel_event.is_set()

    m = scanner.load_or_create_match(team, match)
    if not m.clips:
        raise RuntimeError("Match has no clips to render")

    home_roster = scanner.load_team_roster(team)
    home = TeamBranding(
        name=home_roster.team_name or team,
        color=home_roster.team_color or "#2d8a4e",
    )
    away = TeamBranding(
        name=m.opponent_roster.team_name or m.opponent or "Away",
        color=m.opponent_roster.team_color or "#8a2d2d",
    )

    # Resolve a source frame range when a playhead_frame was supplied.
    source_frame_range: Optional[tuple[int, int]] = None
    if playhead_frame is not None:
        half = float(seconds_around if seconds_around is not None else DEFAULT_PREVIEW_SECONDS_AROUND)
        half_frames = int(round(half * (m.fps or 30.0)))
        total_src = m.total_frames()
        start = max(0, playhead_frame - half_frames)
        end = min(total_src, playhead_frame + half_frames)
        if end <= start:
            raise RuntimeError("Preview window is empty (check playhead_frame / seconds_around)")
        source_frame_range = (start, end)
        logger.info(
            "preview render: playhead=%d half=%.1fs -> source frames [%d, %d)",
            playhead_frame, half, start, end,
        )

    renders = paths.renders_dir(team, match)
    renders.mkdir(parents=True, exist_ok=True)

    label_segment = (label or "render").lower().replace(" ", "_")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_filename = f"{label_segment}_{timestamp}.mp4"
    output_path = renders / output_filename

    media_dir = paths.match_dir(team, match)

    last_pct = [0.0]
    last_emit = [time.time()]

    def on_progress(p: RenderProgress) -> None:
        now = time.time()
        if p.percent - last_pct[0] >= 0.5 or now - last_emit[0] > 0.4:
            JOBS.update(
                job_id,
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
        try:
            output_path.unlink(missing_ok=True)
        except Exception:
            logger.warning("failed to remove partial render output: %s", output_path)
        JOBS.mark_cancelled(job_id)
        raise

    JOBS.mark_done(job_id, output_filename)
