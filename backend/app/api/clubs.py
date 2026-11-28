"""Clubs: opponents set up ahead of time (library/clubs.py).

    GET    /clubs               every club, by name
    POST   /clubs               {name, colors, hint?}
    PUT    /clubs/{id}          {name?, colors?, hint?}
    DELETE /clubs/{id}          matches already made keep their copy
    GET    /clubs/{id}/logo
    PUT    /clubs/{id}/logo     multipart `file`; kept as RGBA PNG
    DELETE /clubs/{id}/logo
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..library import clubs, logo_tools
from ..publish import site
from .logos import _delete_logo, _save_logo, _serve_logo

router = APIRouter(prefix="/clubs", tags=["clubs"])


class ClubIn(BaseModel):
    name: Optional[str] = None
    colors: Optional[list[str]] = None
    hint: Optional[str] = None


class ClubOut(BaseModel):
    id: str
    name: str
    colors: list[str]
    has_logo: bool
    hint: str = ""


def _out(c: clubs.Club) -> ClubOut:
    return ClubOut(id=c.id, name=c.name, colors=c.colors, has_logo=clubs.logo_file(c) is not None,
                   hint=c.hint)


def _get(club_id: str) -> clubs.Club:
    try:
        return clubs.get(club_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


def _as_png(path: Path) -> str:
    """A badge always has an alpha channel, so it sits on any colour
    without a box: anything uploaded is kept as RGBA PNG."""
    try:
        data = logo_tools.png(logo_tools.load(path.read_bytes()))
    except Exception as exc:
        path.unlink(missing_ok=True)
        raise HTTPException(400, f"Not an image VME can read: {exc}") from exc
    target = path.with_suffix(".png")
    if target != path:
        path.unlink(missing_ok=True)
    target.write_bytes(data)
    return target.name


@router.get("", response_model=list[ClubOut])
def list_clubs() -> list[ClubOut]:
    return [_out(c) for c in clubs.list_clubs()]


@router.post("", response_model=ClubOut)
def create_club(body: ClubIn) -> ClubOut:
    try:
        club = clubs.create(body.name or "", body.colors, body.hint)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    site.publish_soon()
    return _out(club)


@router.put("/{club_id}", response_model=ClubOut)
def update_club(club_id: str, body: ClubIn) -> ClubOut:
    _get(club_id)
    try:
        club = clubs.update(club_id, name=body.name, colors=body.colors, hint=body.hint)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    site.publish_soon()
    return _out(club)


@router.delete("/{club_id}")
def delete_club(club_id: str) -> dict:
    _get(club_id)
    deleted = clubs.delete(club_id)
    site.publish_soon()
    return {"deleted": deleted}


@router.get("/{club_id}/logo")
def get_club_logo(club_id: str) -> FileResponse:
    club = _get(club_id)
    return _serve_logo(clubs.club_dir(club_id), club.logo, clubs.LOGO_STEM)


@router.put("/{club_id}/logo", response_model=ClubOut)
def put_club_logo(club_id: str, file: UploadFile = File(...)) -> ClubOut:
    _get(club_id)
    name = _save_logo(clubs.club_dir(club_id), clubs.LOGO_STEM, file)
    name = _as_png(clubs.club_dir(club_id) / name)
    club = clubs.set_logo(club_id, name)
    site.publish_soon()
    return _out(club)


@router.delete("/{club_id}/logo", response_model=ClubOut)
def delete_club_logo(club_id: str) -> ClubOut:
    club = _get(club_id)
    _delete_logo(clubs.club_dir(club_id), club.logo, clubs.LOGO_STEM)
    club = clubs.set_logo(club_id, None)
    site.publish_soon()
    return _out(club)
