"""Game state: snapshot of a volleyball match at a point in time."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional

from .team import Team


class ValidationState(Enum):
    VALID = auto()
    WARNING = auto()
    ERROR = auto()


@dataclass
class GameState:
    """Computed state after applying all events up to and including some event.

    Stored on the event itself so lookups don't require replaying history.
    """

    home_score: int = 0
    away_score: int = 0
    home_sets: int = 0
    away_sets: int = 0

    serving_team: Optional[Team] = None

    home_positions: dict[int, Optional[int]] = field(
        default_factory=lambda: dict.fromkeys(range(1, 7), None)
    )
    away_positions: dict[int, Optional[int]] = field(
        default_factory=lambda: dict.fromkeys(range(1, 7), None)
    )

    game_started: bool = False
    game_ended: bool = False

    point_history: list[bool] = field(default_factory=list)

    validation_state: ValidationState = ValidationState.VALID
    validation_errors: list[str] = field(default_factory=list)

    in_cut_region: bool = False
    ball_served_since_last_score: bool = False
    last_score_frame: Optional[int] = None
    last_cut_end_frame: Optional[int] = None
    last_substitution_frame: Optional[int] = None

    def copy(self) -> "GameState":
        return GameState(
            home_score=self.home_score,
            away_score=self.away_score,
            home_sets=self.home_sets,
            away_sets=self.away_sets,
            serving_team=self.serving_team,
            home_positions=self.home_positions.copy(),
            away_positions=self.away_positions.copy(),
            game_started=self.game_started,
            game_ended=self.game_ended,
            point_history=self.point_history.copy(),
            validation_state=ValidationState.VALID,
            validation_errors=[],
            in_cut_region=self.in_cut_region,
            ball_served_since_last_score=self.ball_served_since_last_score,
            last_score_frame=self.last_score_frame,
            last_cut_end_frame=self.last_cut_end_frame,
            last_substitution_frame=self.last_substitution_frame,
        )

    def get_positions(self, team: Team) -> dict[int, Optional[int]]:
        return self.home_positions if team is Team.HOME else self.away_positions

    def set_position(self, team: Team, position: int, player_number: Optional[int]) -> None:
        if team is Team.HOME:
            self.home_positions[position] = player_number
        else:
            self.away_positions[position] = player_number

    def get_player_at_position(self, team: Team, position: int) -> Optional[int]:
        return self.get_positions(team).get(position)

    def rotate_team(self, team: Team) -> None:
        """Rotate clockwise: 2->1, 3->2, 4->3, 5->4, 6->5, 1->6."""
        positions = self.get_positions(team)
        pos1 = positions[1]
        for i in range(1, 6):
            positions[i] = positions[i + 1]
        positions[6] = pos1

    def reset_positions(self, team: Team) -> None:
        if team is Team.HOME:
            self.home_positions = dict.fromkeys(range(1, 7), None)
        else:
            self.away_positions = dict.fromkeys(range(1, 7), None)

    def reset_scores(self) -> None:
        self.home_score = 0
        self.away_score = 0
        self.point_history = []

    def get_score(self, team: Team) -> int:
        return self.home_score if team is Team.HOME else self.away_score

    def add_point(self, team: Team) -> None:
        if team is Team.HOME:
            self.home_score += 1
            self.point_history.append(True)
        else:
            self.away_score += 1
            self.point_history.append(False)
