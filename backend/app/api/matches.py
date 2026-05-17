"""Match-level endpoints: list, create, fetch, update, delete, rescan."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..domain.player import Player
from ..domain.roster import Roster
from ..library import paths, scanner
from .helpers import load_match_or_404, save_match, serialize_match
from .schemas import (
    CreateMatchIn,
    MatchOut,
    MatchSummaryOut,
    UpdateMatchIn,
)

router = APIRouter(prefix="/teams/{team}/matches", tags=["matches"])


@router.get("", response_model=list[MatchSummaryOut])
def list_team_matches(team: str) -> list[MatchSummaryOut]:
    return [MatchSummaryOut(**vars(s)) for s in scanner.list_matches(team)]


@router.post("", response_model=MatchOut)
def create_match(team: str, body: CreateMatchIn) -> MatchOut:
    if not body.name.strip():
        raise HTTPException(400, "Match name is required")
    m = scanner.create_match(team, body.name, opponent=body.opponent or "", date=body.date or "")
    return serialize_match(team, body.name, m)


@router.get("/{match}", response_model=MatchOut)
def get_match(team: str, match: str) -> MatchOut:
    try:
        m = load_match_or_404(team, match)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    save_match(team, match, m)  # persist any newly-discovered clips
    return serialize_match(team, match, m)


@router.patch("/{match}", response_model=MatchOut)
def patch_match(team: str, match: str, body: UpdateMatchIn) -> MatchOut:
    try:
        m = load_match_or_404(team, match)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc

    if body.opponent is not None:
        m.opponent = body.opponent
    if body.date is not None:
        m.date = body.date
    if body.fps is not None:
        m.fps = body.fps
    if body.opponent_roster is not None:
        m.opponent_roster = Roster(
            team_name=body.opponent_roster.team_name,
            team_color=body.opponent_roster.team_color,
            players=[Player.from_dict(p.model_dump()) for p in body.opponent_roster.players],
        )
    save_match(team, match, m)
    return serialize_match(team, match, m)


@router.delete("/{match}")
def delete_match(team: str, match: str) -> dict[str, bool]:
    scanner.delete_match(team, match)
    return {"deleted": True}


@router.post("/{match}/rescan", response_model=MatchOut)
def rescan_match(team: str, match: str) -> MatchOut:
    if not paths.match_dir(team, match).exists():
        raise HTTPException(404, "Match folder not found")
    m = scanner.load_or_create_match(team, match)
    save_match(team, match, m)
    return serialize_match(team, match, m)
