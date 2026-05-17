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
from .schemas import RenderRequestIn

router = APIRouter(tags=["renders"])


@router.post("/teams/{team}/matches/{match}/renders")
def start_render(team: str, match: str, body: RenderRequestIn) -> dict:
    try:
        load_match_or_404(team, match)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    job = JOBS.create(team=team, match=match, label=body.label or "render")
    kick_off_render(
        job.id,
        team,
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


@router.get("/teams/{team}/matches/{match}/renders/{filename}")
def download_render(team: str, match: str, filename: str) -> FileResponse:
    target = paths.renders_dir(team, match) / filename
    if not target.is_file():
        raise HTTPException(404, "Render file not found")
    return FileResponse(target, media_type="video/mp4", filename=filename)
