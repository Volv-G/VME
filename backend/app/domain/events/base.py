"""MatchEvent base class.

Every concrete event subclass:
  - Sets `type_name: ClassVar[str]` (and is decorated with @register_event).
  - Returns its effects via the four `*_effect` properties.
  - Optionally overrides `apply(state)` to mutate game state (default does
    nothing; the engine derives mutations from `score_effect` / `roster_effect`
    when the event doesn't override).
  - Optionally overrides `validate(state, context)` for custom rules.

Frame storage is clip-relative: every event has a `clip_id` and `local_frame`
(except `ClipTransitionEvent`, which sits at a clip boundary - see timeline.py).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Optional

from ..game_state import GameState, ValidationState
from ..effects import (
    OverlayEffect,
    PointScoredEffect,
    PositionsResetEffect,
    RosterEffect,
    ScoreDeltaEffect,
    ScoreEffect,
    ScoreResetEffect,
    ServeTeamSetEffect,
    SubstitutionApplyEffect,
    TimelineEffect,
    BallServedEffect,
    ReplayMarkerEffect,
)
from ..team import Team


@dataclass
class ValidationError:
    """A validation issue produced while computing state."""

    severity: ValidationState  # WARNING or ERROR
    message: str


@dataclass
class MatchEvent:
    """Base class for all match events.

    Subclasses are dataclasses with their own typed payload fields.
    """

    type_name: ClassVar[str] = "base"

    id: int = 0
    clip_id: str = ""
    local_frame: int = 0

    # Computed snapshot of state AFTER applying this event. Not serialized.
    state: Optional[GameState] = field(default=None, repr=False, compare=False)

    # ---- Effect properties (override in subclasses as needed) -------------

    @property
    def timeline_effect(self) -> Optional[TimelineEffect]:
        return None

    @property
    def overlay_effect(self) -> Optional[OverlayEffect]:
        return None

    @property
    def score_effect(self) -> Optional[ScoreEffect]:
        return None

    @property
    def roster_effect(self) -> Optional[RosterEffect]:
        return None

    # ---- State machine ----------------------------------------------------

    def apply(self, state: GameState, all_events: list["MatchEvent"]) -> GameState:
        """Apply this event to a state, returning the new state.

        Default behavior: derive mutations from `score_effect` and `roster_effect`.
        Subclasses can override for special semantics.

        `all_events` is provided for cross-event validation (e.g. an `AceEvent`
        looking up the current serving team).
        """
        new_state = state.copy()

        score = self.score_effect
        if isinstance(score, PointScoredEffect):
            team = score.team
            if state.serving_team is not None and team is not state.serving_team:
                new_state.rotate_team(team)
            new_state.add_point(team)
            new_state.serving_team = team
            new_state.ball_served_since_last_score = False
            new_state.last_score_frame = self.local_frame
        elif isinstance(score, ScoreDeltaEffect):
            new_state.home_score = max(0, new_state.home_score + score.home_delta)
            new_state.away_score = max(0, new_state.away_score + score.away_delta)
        elif isinstance(score, ScoreResetEffect):
            if new_state.home_score > new_state.away_score:
                new_state.home_sets += 1
            elif new_state.away_score > new_state.home_score:
                new_state.away_sets += 1
            new_state.reset_scores()

        roster = self.roster_effect
        if isinstance(roster, ServeTeamSetEffect):
            new_state.serving_team = roster.team
        elif isinstance(roster, SubstitutionApplyEffect):
            new_state.set_position(roster.team, roster.position, roster.player_in)
            new_state.last_substitution_frame = self.local_frame
        elif isinstance(roster, PositionsResetEffect):
            new_state.reset_positions(Team.HOME)
            new_state.reset_positions(Team.AWAY)
            new_state.serving_team = None
            new_state.ball_served_since_last_score = False
        elif isinstance(roster, BallServedEffect):
            new_state.ball_served_since_last_score = True
        elif isinstance(roster, ReplayMarkerEffect):
            new_state.ball_served_since_last_score = False
            new_state.last_score_frame = self.local_frame

        return new_state

    def validate(
        self, state_before: GameState, all_events: list["MatchEvent"]
    ) -> list[ValidationError]:
        """Return a list of validation errors raised by this event.

        Default: no extra validation beyond what `apply` enforces implicitly.
        """
        return []

    # ---- Serialization ---------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        from .registry import event_to_dict

        return event_to_dict(self)

    @property
    def team(self) -> Optional[Team]:
        """Convenience accessor used by some renderers; returns None if N/A."""
        return getattr(self, "_team", None)
