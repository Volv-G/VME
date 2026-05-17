"""Scoring events that aren't tied to a specific player."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Optional

from ..effects import PointScoredEffect, ScoreDeltaEffect, ScoreEffect
from ..game_state import GameState, ValidationState
from ..team import Team
from .base import MatchEvent, ValidationError
from .registry import register_event


@register_event
@dataclass
class ScoreEvent(MatchEvent):
    """A team scored one point."""

    type_name: ClassVar[str] = "score"

    team: Team = Team.HOME

    @property
    def score_effect(self) -> Optional[ScoreEffect]:
        return PointScoredEffect(team=self.team)

    def validate(self, state_before: GameState, all_events) -> list[ValidationError]:
        if not state_before.ball_served_since_last_score:
            return [
                ValidationError(ValidationState.ERROR, "SCORE without preceding BALL_SERVED")
            ]
        return []


@register_event
@dataclass
class ScoreCorrectionEvent(MatchEvent):
    """Manual score adjustment (positive or negative deltas per team)."""

    type_name: ClassVar[str] = "score_correction"

    home_delta: int = 0
    away_delta: int = 0

    @property
    def score_effect(self) -> Optional[ScoreEffect]:
        return ScoreDeltaEffect(home_delta=self.home_delta, away_delta=self.away_delta)
