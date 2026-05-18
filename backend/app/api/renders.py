"""Render endpoints: enqueue, queue control, progress stream, download.

The match-scoped POST `/teams/{team}/.../renders` now only ENQUEUES the
job - it does not start it. The dispatcher (see `app/jobs/dispatcher.py`)
picks pending jobs up while the queue is `active`.

Queue control lives at the top level (`/queue/start`, `/queue/stop`,
`/queue/state`) because the queue is global - one team's start affects
the whole machine. The per-team list endpoint (`/teams/{team}/jobs`) is
the natural feed for the team-dashboard queue widget.
"""

from __future__ import annotations

import asyncio
import json
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from ..jobs.manager import JOBS, JobStatus
from ..jobs.render_job import kick_off_immediate
from ..library import paths
from .helpers import load_match_or_404
from .schemas import RenderFileOut, RenderRequestIn

logger = logging.getLogger(__name__)

router = APIRouter(tags=["renders"])


# ---- Enqueue ---------------------------------------------------------------


@router.post(
    "/teams/{team}/tournaments/{tournament}/dates/{date}/matches/{match}/renders"
)
def enqueue_render(
    team: str,
    tournament: str,
    date: str,
    match: str,
    body: RenderRequestIn,
) -> dict:
    """Add a render job to the global queue.

    Returns the new job in PENDING status. When `immediate=True` the
    job starts running in its own thread right away (used for previews -
    the user wants instant feedback). Otherwise it stays pending until
    the dispatcher picks it up (see POST /queue/start), so a long full
    render doesn't tie up the machine mid-edit.
    """
    try:
        m = load_match_or_404(team, tournament, date, match)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc

    # Derive display metadata so the queue UI can render meaningful rows
    # without a follow-up fetch per job.
    match_index, _ = paths.parse_match_folder(match)
    kind = body.kind or "full"
    if kind not in {"full", "preview", "highlights", "focused_highlights"}:
        raise HTTPException(400, f"Unknown render kind: {kind!r}")
    job = JOBS.enqueue(
        team=team,
        tournament=tournament,
        date=date,
        match=match,
        label=body.label or kind,
        kind=kind,
        playhead_frame=body.playhead_frame,
        seconds_around=body.seconds_around,
        opponent=m.opponent or "",
        match_index=match_index,
        immediate=body.immediate,
    )
    if body.immediate:
        kick_off_immediate(job)
    return job.to_dict()


# ---- Queue control --------------------------------------------------------


@router.post("/queue/start")
def queue_start() -> dict:
    """Resume the dispatcher: pending jobs start running FIFO."""
    JOBS.set_active(True)
    return JOBS.queue_summary()


@router.post("/queue/stop")
def queue_stop() -> dict:
    """Pause the dispatcher. The currently running job (if any) finishes;
    no new pending job will be picked up until /queue/start."""
    JOBS.set_active(False)
    return JOBS.queue_summary()


@router.get("/queue/state")
def queue_state() -> dict:
    """Lightweight summary: active flag + pending/running counts."""
    return JOBS.queue_summary()


# ---- Jobs listing ---------------------------------------------------------


@router.get("/jobs")
def list_jobs() -> list[dict]:
    """All jobs across all teams. Used by debug / future global widget."""
    return [j.to_dict() for j in JOBS.list_jobs()]


@router.get("/teams/{team}/jobs")
def list_team_jobs(team: str) -> list[dict]:
    """Jobs belonging to `team`, newest first. Feeds the team dashboard
    queue widget."""
    return [j.to_dict() for j in JOBS.list_team_jobs(team)]


@router.get("/jobs/{job_id}")
def get_job(job_id: str) -> dict:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    return job.to_dict()


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict:
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    if not JOBS.request_cancel(job_id):
        raise HTTPException(409, f"Job is {job.status.value}; cannot cancel")
    return JOBS.get(job_id).to_dict()  # type: ignore[union-attr]


@router.delete("/jobs/{job_id}")
def delete_job(job_id: str) -> dict:
    """Remove a terminal job from history. Running/pending must be
    cancelled first - this is a history-cleanup operation only."""
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    if job.status in {JobStatus.PENDING, JobStatus.RUNNING}:
        raise HTTPException(
            409,
            f"Job is {job.status.value}; cancel it first before deleting",
        )
    if not JOBS.delete(job_id):
        # Race: another caller deleted it between get() and delete().
        raise HTTPException(404, "Job not found")
    return {"deleted": job_id}


@router.get("/jobs/{job_id}/events")
async def job_events(job_id: str) -> StreamingResponse:
    """Per-job SSE stream of progress updates."""
    job = JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, "Job not found")
    queue = JOBS.subscribe(job_id)

    async def gen():
        try:
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield f"data: {json.dumps(payload)}\n\n"
                    if payload.get("status") in ("done", "failed", "cancelled"):
                        break
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            JOBS.unsubscribe(job_id, queue)

    return StreamingResponse(gen(), media_type="text/event-stream")


# ---- Render-file management (unchanged) -----------------------------------


@router.get(
    "/teams/{team}/tournaments/{tournament}/dates/{date}/matches/{match}/renders",
    response_model=list[RenderFileOut],
)
def list_renders(
    team: str, tournament: str, date: str, match: str
) -> list[RenderFileOut]:
    """List rendered output files in the match's `renders/` folder.

    Returns an empty list if the folder doesn't exist yet (no renders run).
    Sorted newest first by mtime so the UI can show recent renders at the top.
    """
    load_match_or_404(team, tournament, date, match)
    renders = paths.renders_dir(team, tournament, date, match)
    if not renders.is_dir():
        return []
    # Walk the whole tree so the batch outputs under
    # `highlights/<team>/<player>/...` and `focused/<team>/<player>/...`
    # show up alongside the flat full / preview files. The `filename`
    # field carries the relative path from the renders root, which is
    # also what download/delete expect.
    out: list[RenderFileOut] = []
    for p in renders.rglob("*.mp4"):
        if not p.is_file():
            continue
        try:
            stat = p.stat()
        except OSError:
            continue
        rel = p.relative_to(renders).as_posix()
        out.append(
            RenderFileOut(
                filename=rel,
                size_bytes=stat.st_size,
                created_at=stat.st_mtime,
            )
        )
    out.sort(key=lambda r: r.created_at, reverse=True)
    return out


@router.get(
    "/teams/{team}/tournaments/{tournament}/dates/{date}/matches/{match}/renders/{filename:path}"
)
def download_render(
    team: str,
    tournament: str,
    date: str,
    match: str,
    filename: str,
) -> FileResponse:
    renders = paths.renders_dir(team, tournament, date, match)
    target = _safe_render_path(renders, filename)
    if not target.is_file():
        raise HTTPException(404, "Render file not found")
    # `name` is just the leaf for the Content-Disposition header.
    return FileResponse(target, media_type="video/mp4", filename=target.name)


@router.delete(
    "/teams/{team}/tournaments/{tournament}/dates/{date}/matches/{match}/renders/{filename:path}"
)
def delete_render(
    team: str, tournament: str, date: str, match: str, filename: str
) -> dict:
    """Delete a single render file. The folder itself is left in place."""
    renders = paths.renders_dir(team, tournament, date, match)
    target = _safe_render_path(renders, filename)
    if not target.is_file():
        raise HTTPException(404, "Render file not found")
    target.unlink()
    # Best-effort prune of empty parent dirs (e.g. delete the last
    # highlight for a player and their folder goes away too). Stop at
    # the renders root so we never remove `renders/` itself.
    parent = target.parent
    while parent != renders and parent.is_dir():
        try:
            next(parent.iterdir())
            break  # not empty
        except StopIteration:
            grandparent = parent.parent
            try:
                parent.rmdir()
            except OSError:
                break
            parent = grandparent
    return {"deleted": filename}


def _safe_render_path(renders_root, filename: str):
    """Resolve a user-supplied relative filename inside `renders_root`.

    With nested folders we can't reject `/` blindly any more; instead we
    resolve the final path and confirm it stays inside the renders root.
    Rejects absolute paths, parent-traversal (`..`), and anything that
    escapes the root after normalization.
    """
    from pathlib import Path

    if not filename or filename in (".", ".."):
        raise HTTPException(400, "Invalid filename")
    candidate = (renders_root / filename).resolve()
    try:
        candidate.relative_to(renders_root.resolve())
    except ValueError:
        raise HTTPException(400, "Invalid filename")
    return candidate
