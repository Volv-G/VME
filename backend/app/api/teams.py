"""Team and roster endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..domain.player import Player
from ..domain.roster import Roster
from ..library import scanner
from .schemas import PlayerOut, RosterOut, TeamSummaryOut

router = APIRouter(prefix="/teams", tags=["teams"])


@router.get("", response_model=list[TeamSummaryOut])
def list_teams() -> list[TeamSummaryOut]:
    return [
        TeamSummaryOut(
            name=t.name, has_roster=t.has_roster, tournament_count=t.tournament_count
        )
        for t in scanner.list_teams()
    ]


@router.post("", response_model=TeamSummaryOut)
def create_team(name: str) -> TeamSummaryOut:
    if not name.strip():
        raise HTTPException(400, "Team name is required")
    scanner.create_team(name)
    return next(
        (
            TeamSummaryOut(
                name=t.name, has_roster=t.has_roster, tournament_count=t.tournament_count
            )
            for t in scanner.list_teams()
            if t.name == name or t.name == name.strip()
        ),
        TeamSummaryOut(name=name, has_roster=False, tournament_count=0),
    )


@router.get("/{team}/roster", response_model=RosterOut)
def get_roster(team: str) -> RosterOut:
    r = scanner.load_team_roster(team)
    return RosterOut(
        team_name=r.team_name,
        team_color=r.team_color,
        players=[PlayerOut(**p.to_dict()) for p in r.players],
    )


@router.put("/{team}/roster", response_model=RosterOut)
def put_roster(team: str, roster: RosterOut) -> RosterOut:
    r = Roster(
        team_name=roster.team_name,
        team_color=roster.team_color,
        players=[Player.from_dict(p.model_dump()) for p in roster.players],
    )
    scanner.save_team_roster(team, r)
    return roster
