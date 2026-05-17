"""Render endpoints: kick off a render, stream progress, download output."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from ..jobs.manager import JOBS
from ..jobs.render_job import kick_off_render
from ..library import paths
from .helpers import load_match_or_404
from .schemas import RenderFileOut, RenderRequestIn

router = APIRouter(tags=["renders"])


@router.post(
    "/teams/{team}/tournaments/{tournament}/dates/{date}/matches/{match}/renders"
)
def start_render(
    team: str,
    tournament: str,
    date: str,
    match: str,
    body: RenderRequestIn,
) -> dict:
    try:
        load_match_or_404(team, tournament, date, match)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    job = JOBS.create(
        team=team,
        tournament=tournament,
        date=date,
        match=match,
        label=body.label or "render",
    )
    kick_off_render(
        job.id,
        team,
        tournament,
        date,
        match,
        label=body.label or "",
        playhead_frame=body.playhead_frame,
        seconds_around=body.seconds_around,
    )
    return job.to_dict()


@router.get("/jobs")
def list_jobs() -> list[dict]:
    return [j.to_dict() for j in JOBS.list_jobs()]


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


@router.get("/jobs/{job_id}/events")
async def job_events(job_id: str) -> StreamingResponse:
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
    # Validate the match exists; surfaces a 404 for bad paths.
    load_match_or_404(team, tournament, date, match)
    renders = paths.renders_dir(team, tournament, date, match)
    if not renders.is_dir():
        return []
    out: list[RenderFileOut] = []
    for p in renders.iterdir():
        if not p.is_file() or p.suffix.lower() != ".mp4":
            continue
        try:
            stat = p.stat()
        except OSError:
            continue
        out.append(
            RenderFileOut(
                filename=p.name,
                size_bytes=stat.st_size,
                created_at=stat.st_mtime,
            )
        )
    out.sort(key=lambda r: r.created_at, reverse=True)
    return out


@router.get(
    "/teams/{team}/tournaments/{tournament}/dates/{date}/matches/{match}/renders/{filename}"
)
def download_render(
    team: str,
    tournament: str,
    date: str,
    match: str,
    filename: str,
) -> FileResponse:
    target = paths.renders_dir(team, tournament, date, match) / filename
    if not target.is_file():
        raise HTTPException(404, "Render file not found")
    return FileResponse(target, media_type="video/mp4", filename=filename)


@router.delete(
    "/teams/{team}/tournaments/{tournament}/dates/{date}/matches/{match}/renders/{filename}"
)
def delete_render(
    team: str, tournament: str, date: str, match: str, filename: str
) -> dict:
    """Delete a single render file. The folder itself is left in place."""
    target = paths.renders_dir(team, tournament, date, match) / filename
    # Defensive: refuse path-traversal attempts. `filename` should be a leaf.
    if "/" in filename or "\\" in filename or filename in ("", ".", ".."):
        raise HTTPException(400, "Invalid filename")
    if not target.is_file():
        raise HTTPException(404, "Render file not found")
    target.unlink()
    return {"deleted": filename}
