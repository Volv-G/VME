"""Player statistics over a match, a tournament, or a team's whole season.

Read-only: matches are read straight from their `match.json`, without the
clip reconciliation a match GET does, so asking for a season's numbers
never rewrites a match.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..domain.match import Match
from ..domain.roster import Roster
from ..domain.stats import Stats, column_order, match_stats
from ..library import paths, scanner
from ..render.reels import reels_from_match

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/teams/{team}", tags=["stats"])


class StatsLineOut(BaseModel):
    number: int | None = None
    name: str = ""
    events: dict[str, int]
    serves: int
    serves_won: int
    # Plays in the player's reel: events close together are one play.
    plays: int = 0


class StatsOut(BaseModel):
    matches: int
    # Event types that occur, in display order: the table's columns.
    columns: list[str]
    players: list[StatsLineOut]
    total: StatsLineOut


def _add_match(stats: Stats, path: Path, roster: Roster) -> None:
    try:
        m = Match.load(path)
        one = match_stats(m)
        # Plays as the player reels cut them, so the count matches the
        # "N plays" a reel is named with.
        for reel in reels_from_match(m, roster, roster.team_name or ""):
            n = len(reel.segments)
            one.line(reel.jersey).plays += n
            one.total.plays += n
        stats.add(one)
    except Exception as exc:  # one broken match must not hide the season
        logger.warning("stats: skipping %s: %s", path, exc)


def _add_tournament(stats: Stats, team: str, tournament: str, roster: Roster) -> None:
    for s in scanner.list_matches(team, tournament):
        if s.has_match_json:
            _add_match(
                stats, paths.match_json_path(team, tournament, s.date, s.name), roster
            )


def _out(roster: Roster, stats: Stats) -> StatsOut:
    names = {p.number: p.name for p in roster.players}
    players = [
        StatsLineOut(
            number=n,
            name=names.get(n, ""),
            events=dict(line.events),
            serves=line.serves,
            serves_won=line.serves_won,
            plays=line.plays,
        )
        for n, line in sorted(stats.players.items())
    ]
    return StatsOut(
        matches=stats.matches,
        columns=column_order(stats.total.events),
        players=players,
        total=StatsLineOut(
            name="Team",
            events=dict(stats.total.events),
            serves=stats.total.serves,
            serves_won=stats.total.serves_won,
            plays=stats.total.plays,
        ),
    )


@router.get("/stats", response_model=StatsOut)
def team_stats(team: str) -> StatsOut:
    if not paths.team_dir(team).exists():
        raise HTTPException(404, f"Team not found: {team}")
    roster = scanner.load_team_roster(team)
    stats = Stats()
    for d in paths.iter_tournament_dirs(team):
        _add_tournament(stats, team, d.name, roster)
    return _out(roster, stats)


@router.get("/tournaments/{tournament}/stats", response_model=StatsOut)
def tournament_stats(team: str, tournament: str) -> StatsOut:
    if not paths.tournament_dir(team, tournament).exists():
        raise HTTPException(404, "Tournament not found")
    roster = scanner.load_team_roster(team)
    stats = Stats()
    _add_tournament(stats, team, tournament, roster)
    return _out(roster, stats)


@router.get(
    "/tournaments/{tournament}/dates/{date}/matches/{match}/stats",
    response_model=StatsOut,
)
def match_stats_route(team: str, tournament: str, date: str, match: str) -> StatsOut:
    path = paths.match_json_path(team, tournament, date, match)
    if not path.exists():
        raise HTTPException(404, "Match not found")
    roster = scanner.load_team_roster(team)
    stats = Stats()
    _add_match(stats, path, roster)
    return _out(roster, stats)
