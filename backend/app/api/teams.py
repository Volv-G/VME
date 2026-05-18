"""Team and roster endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..domain.player import Player
from ..domain.roster import NamingConfig, Roster, YouTubeConfig
from ..library import scanner
from .schemas import (
    FullRenderOut,
    NamingConfigOut,
    PlayerOut,
    RosterOut,
    TeamSummaryOut,
    YouTubeConfigOut,
)

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


def _serialize_roster(r: Roster) -> RosterOut:
    """Round-trip a Roster through the API schema, including the
    `youtube` and `naming` blocks. Centralized so GET and PUT stay in
    sync."""
    return RosterOut(
        team_name=r.team_name,
        team_color=r.team_color,
        youtube=YouTubeConfigOut(**r.youtube.to_dict()),
        naming=NamingConfigOut(**r.naming.to_dict()),
        players=[PlayerOut(**p.to_dict()) for p in r.players],
    )


@router.get("/{team}/roster", response_model=RosterOut)
def get_roster(team: str) -> RosterOut:
    return _serialize_roster(scanner.load_team_roster(team))


@router.put("/{team}/roster", response_model=RosterOut)
def put_roster(team: str, roster: RosterOut) -> RosterOut:
    # The request may legitimately omit `youtube` / `naming` (older
    # clients, or a team that hasn't configured those yet). Fall back
    # to the existing saved values in that case rather than wiping any
    # previously-saved settings - reload existing first when EITHER is
    # missing so we don't refetch twice.
    existing = None
    if roster.youtube is None or roster.naming is None:
        existing = scanner.load_team_roster(team)

    yt: YouTubeConfig
    if roster.youtube is None:
        yt = existing.youtube  # type: ignore[union-attr]
    else:
        yt = YouTubeConfig.from_dict(roster.youtube.model_dump())

    naming: NamingConfig
    if roster.naming is None:
        naming = existing.naming  # type: ignore[union-attr]
    else:
        naming = NamingConfig.from_dict(roster.naming.model_dump())

    r = Roster(
        team_name=roster.team_name,
        team_color=roster.team_color,
        youtube=yt,
        naming=naming,
        players=[Player.from_dict(p.model_dump()) for p in roster.players],
    )
    scanner.save_team_roster(team, r)
    return _serialize_roster(r)


@router.get("/{team}/full-renders", response_model=list[FullRenderOut])
def list_full_renders(team: str) -> list[FullRenderOut]:
    """All full-match renders for `team`, across every tournament / date.

    Used by the team dashboard's "Full renders" panel. Each entry
    carries the location info needed to construct download/upload URLs
    plus the YouTube state read from the per-render sidecar.
    """
    return [
        FullRenderOut(
            team=r.team,
            tournament=r.tournament,
            date=r.date,
            match=r.match,
            filename=r.filename,
            size_bytes=r.size_bytes,
            created_at=r.created_at,
            opponent=r.opponent,
            match_index=r.match_index,
            youtube_video_id=r.youtube_video_id,
            youtube_uploaded_at=r.youtube_uploaded_at,
        )
        for r in scanner.list_team_full_renders(team)
    ]
