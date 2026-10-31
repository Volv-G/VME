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
from ..domain.stats import Stats, column_order, match_stats
from ..library import paths, scanner

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/teams/{team}", tags=["stats"])


class StatsLineOut(BaseModel):
    number: int | None = None
    name: str = ""
    events: dict[str, int]
    serves: int
    serves_won: int


class StatsOut(BaseModel):
    matches: int
    # Event types that occur, in display order: the table's columns.
    columns: list[str]
    players: list[StatsLineOut]
    total: StatsLineOut


def _add_match(stats: Stats, path: Path) -> None:
    try:
        stats.add(match_stats(Match.load(path)))
    except Exception as exc:  # one broken match must not hide the season
        logger.warning("stats: skipping %s: %s", path, exc)


def _add_tournament(stats: Stats, team: str, tournament: str) -> None:
    for s in scanner.list_matches(team, tournament):
        if s.has_match_json:
            _add_match(stats, paths.match_json_path(team, tournament, s.date, s.name))


def _out(team: str, stats: Stats) -> StatsOut:
    roster = scanner.load_team_roster(team)
    names = {p.number: p.name for p in roster.players}
    players = [
        StatsLineOut(
            number=n,
            name=names.get(n, ""),
            events=dict(line.events),
            serves=line.serves,
            serves_won=line.serves_won,
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
        ),
    )


@router.get("/stats", response_model=StatsOut)
def team_stats(team: str) -> StatsOut:
    if not paths.team_dir(team).exists():
        raise HTTPException(404, f"Team not found: {team}")
    stats = Stats()
    for d in paths.iter_tournament_dirs(team):
        _add_tournament(stats, team, d.name)
    return _out(team, stats)


@router.get("/tournaments/{tournament}/stats", response_model=StatsOut)
def tournament_stats(team: str, tournament: str) -> StatsOut:
    if not paths.tournament_dir(team, tournament).exists():
        raise HTTPException(404, "Tournament not found")
    stats = Stats()
    _add_tournament(stats, team, tournament)
    return _out(team, stats)


@router.get(
    "/tournaments/{tournament}/dates/{date}/matches/{match}/stats",
    response_model=StatsOut,
)
def match_stats_route(team: str, tournament: str, date: str, match: str) -> StatsOut:
    path = paths.match_json_path(team, tournament, date, match)
    if not path.exists():
        raise HTTPException(404, "Match not found")
    stats = Stats()
    _add_match(stats, path)
    return _out(team, stats)
