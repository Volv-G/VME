"""Match-level endpoints: list, create, fetch, update, delete.

Match identity is `(team, tournament, date, name)` where `name` is the
opponent-derived folder leaf. The `dates/{date}` URL segment is what
makes the (date, opponent) pair routable.

Note: there is intentionally no dedicated `/rescan` endpoint. Every
GET on this resource goes through `load_match_or_404` ->
`scanner.load_or_create_match` -> `reconcile_clips`, which adds any
video files that have appeared in the match folder since the last
load. The frontend gets the same effect just by refetching the match.
"""

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

# Listing / creation: scoped to a tournament (matches are flattened across
# date subfolders so the UI gets one chronological list).
list_router = APIRouter(
    prefix="/teams/{team}/tournaments/{tournament}/matches", tags=["matches"]
)

# Per-match operations: identified by (date, name).
router = APIRouter(
    prefix="/teams/{team}/tournaments/{tournament}/dates/{date}/matches",
    tags=["matches"],
)


@list_router.get("", response_model=list[MatchSummaryOut])
def list_tournament_matches(team: str, tournament: str) -> list[MatchSummaryOut]:
    if not paths.tournament_dir(team, tournament).exists():
        raise HTTPException(404, "Tournament not found")
    return [MatchSummaryOut(**vars(s)) for s in scanner.list_matches(team, tournament)]


@list_router.post("", response_model=MatchOut)
def create_match(team: str, tournament: str, body: CreateMatchIn) -> MatchOut:
    if not body.opponent.strip():
        raise HTTPException(400, "Opponent is required")
    if not body.date.strip():
        raise HTTPException(400, "Date is required")
    if not paths.tournament_dir(team, tournament).exists():
        raise HTTPException(404, "Tournament not found")
    try:
        match_name, m = scanner.create_match(
            team,
            tournament,
            body.date.strip(),
            body.opponent.strip(),
            match_index=body.match_index,
        )
    except scanner.MatchIndexConflict as exc:
        raise HTTPException(409, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return serialize_match(team, tournament, body.date.strip(), match_name, m)


@router.get("/{match}", response_model=MatchOut)
def get_match(team: str, tournament: str, date: str, match: str) -> MatchOut:
    try:
        m = load_match_or_404(team, tournament, date, match)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    save_match(team, tournament, date, match, m)  # persist any newly-discovered clips
    return serialize_match(team, tournament, date, match, m)


@router.patch("/{match}", response_model=MatchOut)
def patch_match(
    team: str, tournament: str, date: str, match: str, body: UpdateMatchIn
) -> MatchOut:
    try:
        m = load_match_or_404(team, tournament, date, match)
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
            # The logo is owned by the /opponent-logo endpoints; a client
            # that omits it here must not wipe an uploaded file.
            team_logo_path=(
                body.opponent_roster.team_logo_path
                or m.opponent_roster.team_logo_path
            ),
            players=[Player.from_dict(p.model_dump()) for p in body.opponent_roster.players],
        )
    save_match(team, tournament, date, match, m)
    return serialize_match(team, tournament, date, match, m)


@router.delete("/{match}")
def delete_match(
    team: str, tournament: str, date: str, match: str
) -> dict[str, bool]:
    scanner.delete_match(team, tournament, date, match)
    return {"deleted": True}



