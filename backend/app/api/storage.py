"""Disk use per match - footage and renders - and cleaning them up.

Footage is the source clips in the match folder plus their preview
proxies (`.proxies/`); renders are everything under `renders/`. Cleaning
up footage keeps `match.json` and its clip list, so the events, the
stats and the record of what was filmed all survive - only the video is
gone, and with it the ability to render again. Cleaning up renders takes
the files' upload records and thumbnails with them, as deleting a single
render does.

A match with a job still queued or running is left alone: the job reads
the footage or the renders, and pulling them out from under it fails it
halfway through at best.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import MEDIA_ROOT, VIDEO_EXTENSIONS
from ..jobs.manager import JOBS, SEASON_REEL_KIND, JobStatus
from ..library import paths, scanner
from ..library.preview_proxy import PROXY_DIR

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/teams/{team}", tags=["storage"])

_LIVE = {JobStatus.PENDING, JobStatus.RUNNING}


class MatchStorageOut(BaseModel):
    tournament: str
    date: str
    match: str
    opponent: str
    match_index: int | None = None
    footage_bytes: int
    footage_files: int
    renders_bytes: int
    renders_files: int
    # Everything else in the folder: match.json, the opponent logo.
    other_bytes: int
    # A full (not condensed) render exists, i.e. the footage has been
    # turned into something worth keeping.
    has_full_render: bool
    # A job for this match is queued or running; cleanup skips it.
    busy: bool


class StorageTotalOut(BaseModel):
    footage_bytes: int = 0
    renders_bytes: int = 0
    other_bytes: int = 0


class StorageOut(BaseModel):
    matches: list[MatchStorageOut]
    total: StorageTotalOut
    # Free space on the disk the media library lives on.
    disk_free_bytes: int | None = None


class CleanupIn(BaseModel):
    footage: bool = False
    renders: bool = False


class CleanupOut(BaseModel):
    freed_bytes: int
    matches: int
    # Matches left alone, with why: a live job, or files that would not
    # delete (open in another program).
    skipped: list[str]


def _tree(root: Path) -> tuple[int, int]:
    """Bytes and file count under `root`."""
    size = count = 0
    if root.is_dir():
        for p in root.rglob("*"):
            try:
                if p.is_file():
                    size += p.stat().st_size
                    count += 1
            except OSError:
                continue
    return size, count


def _footage_files(folder: Path) -> list[Path]:
    return [
        p for p in folder.iterdir()
        if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
    ]


def _busy(team: str, tournament: str, date: str, match: str) -> bool:
    # A season reel reads the footage of every match of the team.
    return any(
        j.status in _LIVE
        and j.team == team
        and (
            j.kind == SEASON_REEL_KIND
            or (j.tournament == tournament and j.date == date and j.match == match)
        )
        for j in JOBS.list_jobs()
    )


def _match_storage(team: str, s: scanner.MatchSummary) -> MatchStorageOut:
    folder = paths.match_dir(team, s.tournament, s.date, s.name)
    renders = folder / "renders"
    proxies = folder / PROXY_DIR
    total, _ = _tree(folder)
    footage = 0
    clips = _footage_files(folder)
    for p in clips:
        try:
            footage += p.stat().st_size
        except OSError:
            pass
    proxy_bytes, _ = _tree(proxies)
    renders_bytes, renders_files = _tree(renders)
    has_full = renders.is_dir() and any(
        p.is_file()
        and scanner._is_full_render(p.name)
        and not scanner.is_condensed_render(p.name)
        for p in renders.iterdir()
    )
    return MatchStorageOut(
        tournament=s.tournament,
        date=s.date,
        match=s.name,
        opponent=s.opponent,
        match_index=s.match_index,
        footage_bytes=footage + proxy_bytes,
        footage_files=len(clips),
        renders_bytes=renders_bytes,
        renders_files=renders_files,
        other_bytes=max(0, total - footage - proxy_bytes - renders_bytes),
        has_full_render=has_full,
        busy=_busy(team, s.tournament, s.date, s.name),
    )


def _team_matches(team: str) -> list[scanner.MatchSummary]:
    out: list[scanner.MatchSummary] = []
    for d in paths.iter_tournament_dirs(team):
        out.extend(scanner.list_matches(team, d.name))
    return out


@router.get("/storage", response_model=StorageOut)
def team_storage(team: str) -> StorageOut:
    if not paths.team_dir(team).exists():
        raise HTTPException(404, f"Team not found: {team}")
    matches = [_match_storage(team, s) for s in _team_matches(team)]
    total = StorageTotalOut(
        footage_bytes=sum(m.footage_bytes for m in matches),
        renders_bytes=sum(m.renders_bytes for m in matches),
        other_bytes=sum(m.other_bytes for m in matches),
    )
    try:
        free = shutil.disk_usage(MEDIA_ROOT).free
    except OSError:
        free = None
    return StorageOut(matches=matches, total=total, disk_free_bytes=free)


def _remove(p: Path, failed: list[Path]) -> None:
    try:
        if p.is_dir():
            shutil.rmtree(p, onerror=lambda _f, path, _e: failed.append(Path(path)))
        else:
            p.unlink(missing_ok=True)
    except OSError:
        failed.append(p)


def _clean(
    team: str, tournament: str, date: str, match: str, body: CleanupIn
) -> tuple[int, str | None]:
    """Delete what `body` asks for in one match. Returns the bytes freed
    and, if something was left behind, why."""
    if _busy(team, tournament, date, match):
        return 0, "a render or upload for it is queued or running"
    folder = paths.match_dir(team, tournament, date, match)
    targets: list[Path] = []
    if body.footage:
        targets += _footage_files(folder)
        targets.append(folder / PROXY_DIR)
    if body.renders:
        targets.append(folder / "renders")
    before, _ = _tree(folder)
    failed: list[Path] = []
    for p in targets:
        if p.exists():
            _remove(p, failed)
    after, _ = _tree(folder)
    freed = max(0, before - after)
    logger.info(
        "storage cleanup %s/%s/%s/%s (footage=%s renders=%s): freed %d bytes%s",
        team, tournament, date, match, body.footage, body.renders, freed,
        f", {len(failed)} file(s) would not delete" if failed else "",
    )
    if failed:
        return freed, f"{len(failed)} file(s) would not delete, e.g. {failed[0].name}"
    return freed, None


@router.post("/storage/cleanup", response_model=CleanupOut)
def cleanup_team(team: str, body: CleanupIn) -> CleanupOut:
    if not paths.team_dir(team).exists():
        raise HTTPException(404, f"Team not found: {team}")
    freed = 0
    skipped: list[str] = []
    matches = _team_matches(team)
    for s in matches:
        n, why = _clean(team, s.tournament, s.date, s.name, body)
        freed += n
        if why:
            skipped.append(f"{s.date} {s.name}: {why}")
    return CleanupOut(freed_bytes=freed, matches=len(matches), skipped=skipped)


@router.post(
    "/tournaments/{tournament}/dates/{date}/matches/{match}/storage/cleanup",
    response_model=CleanupOut,
)
def cleanup_match(
    team: str, tournament: str, date: str, match: str, body: CleanupIn
) -> CleanupOut:
    if not paths.match_dir(team, tournament, date, match).is_dir():
        raise HTTPException(404, "Match not found")
    if _busy(team, tournament, date, match):
        raise HTTPException(
            409, "A render or upload for this match is queued or running."
        )
    freed, why = _clean(team, tournament, date, match, body)
    return CleanupOut(freed_bytes=freed, matches=1, skipped=[why] if why else [])
