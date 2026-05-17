"""Serve flow events: FirstServe, BallServed, Replay."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Optional

from ..effects import (
    BallServedEffect,
    ReplayMarkerEffect,
    RosterEffect,
    ServeTeamSetEffect,
)
from ..game_state import GameState, ValidationState
from ..team import Team
from .base import MatchEvent, ValidationError
from .registry import register_event


@register_event
@dataclass
class FirstServeEvent(MatchEvent):
    """Designate which team serves first in a set."""

    type_name: ClassVar[str] = "first_serve"

    team: Team = Team.HOME

    @property
    def roster_effect(self) -> Optional[RosterEffect]:
        return ServeTeamSetEffect(team=self.team)


@register_event
@dataclass
class BallServedEvent(MatchEvent):
    """Mark that a serve has occurred (used to validate later SCORE events)."""

    type_name: ClassVar[str] = "ball_served"

    @property
    def roster_effect(self) -> Optional[RosterEffect]:
        return BallServedEffect()

    def validate(self, state_before: GameState, all_events) -> list[ValidationError]:
        ref = max(
            state_before.last_score_frame or 0,
            state_before.last_cut_end_frame or 0,
            state_before.last_substitution_frame or 0,
        )
        if ref == 0:
            return []
        gap = self.local_frame - ref
        if gap > 16 * 30:
            return [
                ValidationError(
                    ValidationState.WARNING,
                    f"BALL_SERVED unusually late after last score/sub/cut ({gap} frames)",
                )
            ]
        return []


@register_event
@dataclass
class ReplayEvent(MatchEvent):
    """Rally was replayed; resets the served-since-last-score flag."""

    type_name: ClassVar[str] = "replay"

    @property
    def roster_effect(self) -> Optional[RosterEffect]:
        return ReplayMarkerEffect()

    def validate(self, state_before: GameState, all_events) -> list[ValidationError]:
        if not state_before.ball_served_since_last_score:
            return [
                ValidationError(
                    ValidationState.ERROR, "REPLAY without preceding BALL_SERVED"
                )
            ]
        return []
