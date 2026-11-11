"""Season reels: list them per player, render one, upload one to OneDrive.

A season reel is a team-level file (`<team>/season_reels/<NN>_<player>/`),
so these routes sit under the team rather than under a match. Rendering
and uploading both go through the job queue: the render in the render
lane, the upload in the OneDrive lane, as separate actions - the same
split as a match's reels.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..config import VIDEO_EXTENSIONS
from ..jobs.manager import JOBS, SEASON_REEL_KIND, SEASON_UPLOAD_KIND, JobStatus
from ..jobs.season_reel import player_label, season_matches
from ..library import paths, scanner
from ..render.thumbnail import thumbnail_path

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/teams/{team}/season-reels", tags=["season-reels"])

_LIVE = {JobStatus.PENDING, JobStatus.RUNNING}


class SeasonPlayerOut(BaseModel):
    number: int
    label: str
    # Plays across every match with footage, and in how many matches.
    plays: int
    matches: int
    # "queued" / "rendering" while a season render for them is in the queue.
    job_state: Optional[str] = None


class SeasonReelFileOut(BaseModel):
    # Relative to the season reels folder: `<NN>_<player>/<file>`.
    filename: str
    number: Optional[int] = None
    label: str
    size_bytes: int
    created_at: float
    # Still being written by a running job.
    rendering: bool = False
    has_thumbnail: bool = False
    # Matches in the reel, from its timestamp index.
    matches: int = 0
    onedrive_url: Optional[str] = None
    onedrive_uploaded_at: Optional[float] = None
    # "queued" / "uploading" while an upload is in the queue.
    upload_state: Optional[str] = None


class SeasonReelsOut(BaseModel):
    players: list[SeasonPlayerOut]
    files: list[SeasonReelFileOut]
    # Matches with plays that cannot be rendered: their footage is gone.
    no_footage: list[str]


class RenderIn(BaseModel):
    jersey: int


class UploadIn(BaseModel):
    filename: str


def _team_or_404(team: str) -> None:
    if not paths.team_dir(team).exists():
        raise HTTPException(404, f"Team not found: {team}")


def _live_jobs(team: str, kind: str):
    return [j for j in JOBS.list_team_jobs(team) if j.kind == kind and j.status in _LIVE]


def _state(j) -> str:
    if j.kind == SEASON_UPLOAD_KIND:
        return "uploading" if j.status == JobStatus.RUNNING else "queued"
    return "rendering" if j.status == JobStatus.RUNNING else "queued"


def _safe_file(team: str, filename: str) -> Path:
    root = paths.season_reels_dir(team).resolve()
    target = (root / filename).resolve()
    if not filename or root not in target.parents:
        raise HTTPException(400, "Invalid filename")
    return target


@router.get("", response_model=SeasonReelsOut)
def list_season_reels(team: str) -> SeasonReelsOut:
    _team_or_404(team)
    roster = scanner.load_team_roster(team)
    found, no_footage = season_matches(team, roster)
    plays: dict[int, int] = {}
    matches: dict[int, int] = {}
    for sm in found:
        plays[sm.reel.jersey] = plays.get(sm.reel.jersey, 0) + len(sm.reel.segments)
        matches[sm.reel.jersey] = matches.get(sm.reel.jersey, 0) + 1

    renders = _live_jobs(team, SEASON_REEL_KIND)
    render_state = {int((j.payload or {}).get("jersey", -1)): _state(j) for j in renders}

    numbers = sorted({p.number for p in roster.players} | set(plays))
    players = [
        SeasonPlayerOut(
            number=n,
            label=player_label(roster, n)[0],
            plays=plays.get(n, 0),
            matches=matches.get(n, 0),
            job_state=render_state.get(n),
        )
        for n in numbers
    ]
    return SeasonReelsOut(players=players, files=_files(team, roster), no_footage=no_footage)


@router.get("/files", response_model=list[SeasonReelFileOut])
def list_season_reel_files(team: str) -> list[SeasonReelFileOut]:
    """The finished reels alone, without counting anyone's plays: cheap
    enough for the renders list to poll."""
    _team_or_404(team)
    return _files(team, scanner.load_team_roster(team))


def _files(team: str, roster) -> list[SeasonReelFileOut]:
    renders = _live_jobs(team, SEASON_REEL_KIND)
    uploads = _live_jobs(team, SEASON_UPLOAD_KIND)
    writing = {j.output_filename for j in renders if j.status == JobStatus.RUNNING}
    upload_state = {str((j.payload or {}).get("filename")): _state(j) for j in uploads}

    files: list[SeasonReelFileOut] = []
    root = paths.season_reels_dir(team)
    if root.is_dir():
        for folder in sorted(root.iterdir()):
            if not folder.is_dir():
                continue
            number, _ = paths.parse_player_folder(folder.name)
            for p in sorted(folder.iterdir()):
                if (
                    not p.is_file()
                    or p.suffix.lower() not in VIDEO_EXTENSIONS
                    or paths.is_render_scratch(p.name)
                ):
                    continue
                rel = p.relative_to(root).as_posix()
                stat = p.stat()
                chapters = p.with_suffix(p.suffix + ".chapters.txt")
                n_matches = 0
                if chapters.is_file():
                    n_matches = len(chapters.read_text(encoding="utf-8").strip().splitlines())
                od = scanner.load_onedrive_sidecar(p)
                files.append(
                    SeasonReelFileOut(
                        filename=rel,
                        number=number,
                        label=player_label(roster, number)[0] if number is not None else folder.name,
                        size_bytes=stat.st_size,
                        created_at=stat.st_mtime,
                        rendering=rel in writing,
                        has_thumbnail=thumbnail_path(p).is_file(),
                        matches=n_matches,
                        onedrive_url=(od.get("share_url") or od.get("web_url")) if od else None,
                        onedrive_uploaded_at=od.get("uploaded_at") if od else None,
                        upload_state=upload_state.get(rel),
                    )
                )
    files.sort(key=lambda f: f.created_at, reverse=True)
    return files


@router.post("")
def render_season_reel(team: str, body: RenderIn) -> dict:
    _team_or_404(team)
    if any(int((j.payload or {}).get("jersey", -1)) == body.jersey
           for j in _live_jobs(team, SEASON_REEL_KIND)):
        raise HTTPException(409, "A season reel for this player is already queued.")
    roster = scanner.load_team_roster(team)
    label, _ = player_label(roster, body.jersey)
    job = JOBS.enqueue(
        team, "", "", "",
        kind=SEASON_REEL_KIND,
        label=f"Season reel: {label}",
        opponent=label,
        payload={"jersey": body.jersey},
    )
    return job.to_dict()


@router.post("/upload")
def upload_season_reel(team: str, body: UploadIn) -> dict:
    _team_or_404(team)
    target = _safe_file(team, body.filename)
    if not target.is_file():
        raise HTTPException(404, "Season reel not found")
    if body.filename in {str((j.payload or {}).get("filename"))
                         for j in _live_jobs(team, SEASON_UPLOAD_KIND)}:
        raise HTTPException(409, "This season reel is already queued for upload.")
    roster = scanner.load_team_roster(team)
    number, _ = paths.parse_player_folder(Path(body.filename).parts[0])
    label = player_label(roster, number)[0] if number is not None else body.filename
    job = JOBS.enqueue(
        team, "", "", "",
        kind=SEASON_UPLOAD_KIND,
        label=f"Season reel to OneDrive: {label}",
        opponent=label,
        payload={"filename": body.filename},
    )
    return job.to_dict()


@router.get("/files/{filename:path}/thumbnail")
def season_reel_thumbnail(team: str, filename: str) -> FileResponse:
    thumb = thumbnail_path(_safe_file(team, filename))
    if not thumb.is_file():
        raise HTTPException(404, "No thumbnail for this season reel")
    return FileResponse(thumb, media_type="image/jpeg", filename=thumb.name)


@router.get("/files/{filename:path}")
def season_reel_file(team: str, filename: str, inline: bool = False) -> FileResponse:
    target = _safe_file(team, filename)
    if not target.is_file():
        raise HTTPException(404, "Season reel not found")
    return FileResponse(
        target,
        media_type="video/quicktime" if target.suffix.lower() == ".mov" else "video/mp4",
        filename=target.name,
        content_disposition_type="inline" if inline else "attachment",
    )


@router.delete("/files/{filename:path}")
def delete_season_reel(team: str, filename: str) -> dict:
    target = _safe_file(team, filename)
    if not target.is_file():
        raise HTTPException(404, "Season reel not found")
    if any(j.output_filename == filename and j.status == JobStatus.RUNNING
           for j in _live_jobs(team, SEASON_REEL_KIND)):
        raise HTTPException(409, "This season reel is still being rendered.")
    target.unlink()
    for sidecar in (
        thumbnail_path(target),
        target.with_suffix(target.suffix + ".chapters.txt"),
        paths.onedrive_sidecar_path(target),
    ):
        try:
            sidecar.unlink(missing_ok=True)
        except OSError:
            pass
    try:
        target.parent.rmdir()  # only succeeds when it was the last file
    except OSError:
        pass
    return {"deleted": filename}
