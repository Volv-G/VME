"""Player statistics: what each of our players did, over one match or many.

Counted from the event log as it stands, so a stat is exactly as good as
the tagging behind it:

- Every player event credited to one of OUR players (kill, ace, block,
  block point, dig, dive, assist, highlight) counts once for that player.
  The opponent's credited events are not ours to report.
- A serve is a rally that ended in a point while we were serving, and is
  credited to whoever stood at position 1 at the time - the same rule the
  phone uses to credit an ace. "Won" means we took that point. A rally
  redone (`replay`) never produced a point, so it is not a serve here,
  and a rally served with nobody in position 1 counts towards the team
  total only.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .events.base import MatchEvent
from .events.player import PlayerEvent
from .game_state import GameState
from .match import Match
from .team import Team

# Display order of the per-type columns; types not listed sort after.
EVENT_ORDER = (
    "kill", "ace", "block_point", "block", "assist", "dig", "dive", "highlight",
)


@dataclass
class PlayerLine:
    events: Counter = field(default_factory=Counter)
    serves: int = 0
    serves_won: int = 0

    def add(self, other: "PlayerLine") -> None:
        self.events.update(other.events)
        self.serves += other.serves
        self.serves_won += other.serves_won


@dataclass
class Stats:
    matches: int = 0
    # Jersey number -> line. Only our players.
    players: dict[int, PlayerLine] = field(default_factory=dict)
    # The whole team, including serves nobody could be credited with.
    total: PlayerLine = field(default_factory=PlayerLine)

    def line(self, number: int) -> PlayerLine:
        return self.players.setdefault(number, PlayerLine())

    def add(self, other: "Stats") -> None:
        self.matches += other.matches
        for n, line in other.players.items():
            self.line(n).add(line)
        self.total.add(other.total)


def _is_point(before: GameState, after: GameState) -> Optional[bool]:
    """Did this event score a point, and was it ours? None if no point.

    A point is one more entry in the set's point history: a set end
    clears the history, and a score correction moves the numbers
    without adding to it, so neither is mistaken for a rally won.
    """
    if after.home_sets + after.away_sets != before.home_sets + before.away_sets:
        return None
    if len(after.point_history) != len(before.point_history) + 1:
        return None
    return after.point_history[-1]


def match_stats(match: Match) -> Stats:
    stats = Stats(matches=1)
    state = match.initial_state()
    events: Iterable[MatchEvent] = match.events
    for e in events:
        after = e.state if e.state is not None else state
        if isinstance(e, PlayerEvent) and e.team == Team.HOME and e.player_number:
            stats.line(e.player_number).events[e.type_name] += 1
            stats.total.events[e.type_name] += 1
        won = _is_point(state, after)
        if won is not None and state.serving_team == Team.HOME:
            server = state.home_positions.get(1)
            lines = [stats.total] + ([stats.line(server)] if server else [])
            for line in lines:
                line.serves += 1
                line.serves_won += int(won)
        state = after
    return stats


def column_order(types: Iterable[str]) -> list[str]:
    rank = {t: i for i, t in enumerate(EVENT_ORDER)}
    return sorted(set(types), key=lambda t: (rank.get(t, len(rank)), t))
