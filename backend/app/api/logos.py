"""Team logo upload / download / delete.

Two logos exist per match:

* the **team** logo, stored in the team folder and referenced from
  `roster.json` (`team_logo_path`) - shared by every match of that team;
* the **opponent** logo, stored in the match folder and referenced from
  that match's `match.json` (`opponent_roster.team_logo_path`) - the
  opponent changes match to match, so it can't live at team level.

Both are stored under a fixed stem (`logo` / `opponent_logo`) with the
uploaded file's extension, so a new upload REPLACES the old one instead
of accumulating files. The roster stores only the filename, relative to
its own directory, keeping the media tree movable.

Handlers here are deliberately thin wrappers around `_save_logo` /
`_serve_logo` so the team-level and match-level variants can't drift.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from dataclasses import replace

from ..library import paths, scanner
from .helpers import load_match_or_404, save_match

logger = logging.getLogger(__name__)

router = APIRouter(tags=["logos"])

# Logos are small; anything larger is a mistake (a full-size photo) and
# would bloat both the media tree and the thumbnail compositing step.
MAX_LOGO_BYTES = 8 * 1024 * 1024

_MEDIA_TYPES = {
    ".png": "image/png",
    ".webp": "image/webp",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
}


def _save_logo(directory: Path, stem: str, upload: UploadFile) -> str:
    """Persist `upload` as `<directory>/<stem><ext>`; return the filename.

    Any previously-stored logo for the same stem is removed first, even
    when the extension differs - otherwise switching png -> webp would
    leave the old file behind and `find_logo` could resolve either.
    """
    ext = Path(upload.filename or "").suffix.lower()
    if ext not in paths.LOGO_EXTENSIONS:
        raise HTTPException(
            400,
            f"Unsupported image type {ext or '(none)'!r}. "
            f"Allowed: {', '.join(sorted(paths.LOGO_EXTENSIONS))}",
        )
    data = upload.file.read(MAX_LOGO_BYTES + 1)
    if not data:
        raise HTTPException(400, "Uploaded file is empty")
    if len(data) > MAX_LOGO_BYTES:
        raise HTTPException(
            413, f"Logo is larger than {MAX_LOGO_BYTES // (1024 * 1024)} MB"
        )

    directory.mkdir(parents=True, exist_ok=True)
    previous = paths.find_logo(directory, stem)
    if previous is not None:
        try:
            previous.unlink()
        except OSError:
            logger.warning("could not remove previous logo %s", previous)

    target = directory / f"{stem}{ext}"
    target.write_bytes(data)
    logger.info("saved logo %s (%d bytes)", target, len(data))
    return target.name


def _serve_logo(directory: Path, filename: Optional[str], stem: str) -> FileResponse:
    """Return the stored logo, falling back to a stem scan.

    The scan covers rosters written before this feature (no recorded
    filename) and any case where the JSON and the disk drifted apart.
    """
    candidate = (directory / Path(filename).name) if filename else None
    if candidate is None or not candidate.is_file():
        candidate = paths.find_logo(directory, stem)
    if candidate is None or not candidate.is_file():
        raise HTTPException(404, "No logo set")
    return FileResponse(
        candidate,
        media_type=_MEDIA_TYPES.get(candidate.suffix.lower(), "image/png"),
        filename=candidate.name,
    )


def _delete_logo(directory: Path, filename: Optional[str], stem: str) -> bool:
    target = (directory / Path(filename).name) if filename else None
    if target is None or not target.is_file():
        target = paths.find_logo(directory, stem)
    if target is None or not target.is_file():
        return False
    try:
        target.unlink()
        return True
    except OSError:
        logger.warning("could not delete logo %s", target, exc_info=True)
        return False


# ---------------------------------------------------------------------------
# Team logo (roster.json)
# ---------------------------------------------------------------------------


@router.get("/teams/{team}/logo")
def get_team_logo(team: str) -> FileResponse:
    roster = scanner.load_team_roster(team)
    return _serve_logo(
        paths.team_dir(team), roster.team_logo_path, paths.TEAM_LOGO_STEM
    )


@router.put("/teams/{team}/logo")
def put_team_logo(team: str, file: UploadFile = File(...)) -> dict:
    roster = scanner.load_team_roster(team)
    name = _save_logo(paths.team_dir(team), paths.TEAM_LOGO_STEM, file)
    roster.team_logo_path = name
    scanner.save_team_roster(team, roster)
    return {"team_logo_path": name}


@router.delete("/teams/{team}/logo")
def delete_team_logo(team: str) -> dict:
    roster = scanner.load_team_roster(team)
    removed = _delete_logo(
        paths.team_dir(team), roster.team_logo_path, paths.TEAM_LOGO_STEM
    )
    roster.team_logo_path = None
    scanner.save_team_roster(team, roster)
    return {"deleted": removed}


# ---------------------------------------------------------------------------
# Player photos (roster.json -> players[].profile_pic_path)
# ---------------------------------------------------------------------------
#
# Same storage contract as team logos, one folder deeper: the file is
# `players/<NN>.<ext>` inside the team folder and the roster stores that
# relative path. Keyed by jersey number, which is the roster's own
# identity for a player - renaming someone keeps their photo.


def _player_or_404(team: str, number: int):
    roster = scanner.load_team_roster(team)
    player = roster.find_by_number(number)
    if player is None:
        raise HTTPException(404, f"No player #{number} on the {team} roster")
    return roster, player


def _save_player_photo_path(roster, number: int, value: Optional[str]) -> None:
    """Replace the photo path on the matching player (Player is frozen)."""
    roster.players = [
        replace(p, profile_pic_path=value) if p.number == number else p
        for p in roster.players
    ]


@router.get("/teams/{team}/players/{number}/photo")
def get_player_photo(team: str, number: int) -> FileResponse:
    _, player = _player_or_404(team, number)
    return _serve_logo(
        paths.player_photo_dir(team),
        Path(player.profile_pic_path).name if player.profile_pic_path else None,
        paths.player_photo_stem(number),
    )


@router.put("/teams/{team}/players/{number}/photo")
def put_player_photo(
    team: str, number: int, file: UploadFile = File(...)
) -> dict:
    roster, _ = _player_or_404(team, number)
    name = _save_logo(
        paths.player_photo_dir(team), paths.player_photo_stem(number), file
    )
    rel = f"{paths.PLAYER_PHOTO_SUBDIR}/{name}"
    _save_player_photo_path(roster, number, rel)
    scanner.save_team_roster(team, roster)
    return {"profile_pic_path": rel}


@router.delete("/teams/{team}/players/{number}/photo")
def delete_player_photo(team: str, number: int) -> dict:
    roster, player = _player_or_404(team, number)
    removed = _delete_logo(
        paths.player_photo_dir(team),
        Path(player.profile_pic_path).name if player.profile_pic_path else None,
        paths.player_photo_stem(number),
    )
    _save_player_photo_path(roster, number, None)
    scanner.save_team_roster(team, roster)
    return {"deleted": removed}


# ---------------------------------------------------------------------------
# Opponent logo (match.json -> opponent_roster)
# ---------------------------------------------------------------------------


@router.get(
    "/teams/{team}/tournaments/{tournament}/dates/{date}/matches/{match}"
    "/opponent-logo"
)
def get_opponent_logo(
    team: str, tournament: str, date: str, match: str
) -> FileResponse:
    m = load_match_or_404(team, tournament, date, match)
    return _serve_logo(
        paths.match_dir(team, tournament, date, match),
        m.opponent_roster.team_logo_path,
        paths.OPPONENT_LOGO_STEM,
    )


@router.put(
    "/teams/{team}/tournaments/{tournament}/dates/{date}/matches/{match}"
    "/opponent-logo"
)
def put_opponent_logo(
    team: str,
    tournament: str,
    date: str,
    match: str,
    file: UploadFile = File(...),
) -> dict:
    m = load_match_or_404(team, tournament, date, match)
    name = _save_logo(
        paths.match_dir(team, tournament, date, match),
        paths.OPPONENT_LOGO_STEM,
        file,
    )
    m.opponent_roster.team_logo_path = name
    save_match(team, tournament, date, match, m)
    return {"team_logo_path": name}


@router.delete(
    "/teams/{team}/tournaments/{tournament}/dates/{date}/matches/{match}"
    "/opponent-logo"
)
def delete_opponent_logo(
    team: str, tournament: str, date: str, match: str
) -> dict:
    m = load_match_or_404(team, tournament, date, match)
    removed = _delete_logo(
        paths.match_dir(team, tournament, date, match),
        m.opponent_roster.team_logo_path,
        paths.OPPONENT_LOGO_STEM,
    )
    m.opponent_roster.team_logo_path = None
    save_match(team, tournament, date, match, m)
    return {"deleted": removed}
