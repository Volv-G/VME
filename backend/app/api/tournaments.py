"""Tournament endpoints: list / create / delete tournaments under a team."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..domain.tournament import Tournament
from ..library import paths, scanner
from .schemas import CreateTournamentIn, TournamentInfoOut, TournamentSummaryOut

router = APIRouter(prefix="/teams/{team}/tournaments", tags=["tournaments"])


@router.get("", response_model=list[TournamentSummaryOut])
def list_tournaments(team: str) -> list[TournamentSummaryOut]:
    if not paths.team_dir(team).exists():
        raise HTTPException(404, f"Team not found: {team}")
    return [
        TournamentSummaryOut(team=t.team, name=t.name, match_count=t.match_count)
        for t in scanner.list_tournaments(team)
    ]


@router.post("", response_model=TournamentSummaryOut)
def create_tournament(team: str, body: CreateTournamentIn) -> TournamentSummaryOut:
    if not paths.team_dir(team).exists():
        raise HTTPException(404, f"Team not found: {team}")
    if not body.name.strip():
        raise HTTPException(400, "Tournament name is required")
    scanner.create_tournament(team, body.name)
    return next(
        (
            TournamentSummaryOut(team=t.team, name=t.name, match_count=t.match_count)
            for t in scanner.list_tournaments(team)
            if t.name == body.name or t.name == body.name.strip()
        ),
        TournamentSummaryOut(team=team, name=body.name, match_count=0),
    )


@router.delete("/{tournament}")
def delete_tournament(team: str, tournament: str) -> dict[str, bool]:
    if not paths.tournament_dir(team, tournament).exists():
        raise HTTPException(404, "Tournament not found")
    scanner.delete_tournament(team, tournament)
    return {"deleted": True}


@router.get("/{tournament}/info", response_model=TournamentInfoOut)
def get_tournament_info(team: str, tournament: str) -> TournamentInfoOut:
    """Read the tournament.json sidecar (abbreviation / full_name).

    Returns empty defaults if the sidecar doesn't exist; the client can
    then fall back to the folder-slug pretty-printed string.
    """
    if not paths.tournament_dir(team, tournament).exists():
        raise HTTPException(404, "Tournament not found")
    info = scanner.load_tournament_info(team, tournament)
    return TournamentInfoOut(
        abbreviation=info.abbreviation, full_name=info.full_name
    )


@router.put("/{tournament}/info", response_model=TournamentInfoOut)
def put_tournament_info(
    team: str, tournament: str, body: TournamentInfoOut
) -> TournamentInfoOut:
    """Write the tournament.json sidecar. Empty strings -> None so an
    explicit "clear this field" round-trip works."""
    if not paths.tournament_dir(team, tournament).exists():
        raise HTTPException(404, "Tournament not found")
    info = Tournament(
        abbreviation=(body.abbreviation or None),
        full_name=(body.full_name or None),
    )
    scanner.save_tournament_info(team, tournament, info)
    return TournamentInfoOut(
        abbreviation=info.abbreviation, full_name=info.full_name
    )
