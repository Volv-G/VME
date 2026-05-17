"""Stream raw clip files to the browser with HTTP Range support."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Iterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..library import paths
from .helpers import load_match_or_404

router = APIRouter(
    prefix="/teams/{team}/tournaments/{tournament}/dates/{date}/matches/{match}/media",
    tags=["media"],
)

CHUNK_SIZE = 1024 * 1024


@router.get("/clips/{clip_id}/stream")
def stream_clip(
    team: str,
    tournament: str,
    date: str,
    match: str,
    clip_id: str,
    request: Request,
):
    m = load_match_or_404(team, tournament, date, match)
    clip = m.get_clip(clip_id)
    if clip is None:
        raise HTTPException(404, "Clip not found")
    file_path = paths.match_dir(team, tournament, date, match) / clip.filename
    if not file_path.is_file():
        raise HTTPException(404, "Clip file missing on disk")

    return _range_response(file_path, request)


def _range_response(path: Path, request: Request) -> StreamingResponse:
    file_size = path.stat().st_size
    range_header = request.headers.get("range")
    start, end = 0, file_size - 1
    status = 200

    if range_header and range_header.startswith("bytes="):
        try:
            spec = range_header.removeprefix("bytes=")
            r_start, r_end = (spec.split("-") + [""])[:2]
            if r_start:
                start = int(r_start)
            if r_end:
                end = int(r_end)
            status = 206
        except ValueError:
            raise HTTPException(416, "Invalid Range header")
        end = min(end, file_size - 1)
        if start > end:
            raise HTTPException(416, "Invalid Range header")

    length = end - start + 1
    media_type = _guess_media_type(path)

    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(length),
        "Content-Type": media_type,
    }
    if status == 206:
        headers["Content-Range"] = f"bytes {start}-{end}/{file_size}"

    def streamer() -> Iterator[bytes]:
        with path.open("rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                read = min(CHUNK_SIZE, remaining)
                chunk = f.read(read)
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    return StreamingResponse(streamer(), status_code=status, headers=headers, media_type=media_type)


def _guess_media_type(path: Path) -> str:
    ext = path.suffix.lower()
    return {
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".mkv": "video/x-matroska",
        ".m4v": "video/x-m4v",
        ".webm": "video/webm",
        ".avi": "video/x-msvideo",
    }.get(ext, "application/octet-stream")
