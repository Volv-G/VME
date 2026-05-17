"""Player events: kills, aces, assists, blocks, dives, digs, highlights."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Optional

from ..effects import (
    OverlayEffect,
    PlayerPopupEffect,
    PointScoredEffect,
    ScoreEffect,
)
from ..game_state import GameState, ValidationState
from ..team import Team
from .base import MatchEvent, ValidationError
from .registry import register_event


@dataclass
class PlayerEvent(MatchEvent):
    """Base for events that reference a specific player on a specific team."""

    team: Team = Team.HOME
    player_number: int = 0

    # Subclasses override to label the popup (e.g. "Kill", "Ace").
    action_label: ClassVar[str] = "Highlight"

    @property
    def overlay_effect(self) -> Optional[OverlayEffect]:
        return PlayerPopupEffect(
            text=type(self).action_label,
            player_number=self.player_number,
            team=self.team,
        )


@dataclass
class ScoringPlayerEvent(PlayerEvent):
    """Player event that also scores a point for `self.team`."""

    @property
    def score_effect(self) -> Optional[ScoreEffect]:
        return PointScoredEffect(team=self.team)


@dataclass
class StatPlayerEvent(PlayerEvent):
    """Player event that records a stat without affecting the score."""


@register_event
@dataclass
class KillEvent(ScoringPlayerEvent):
    """A kill: scores a point for `team`."""

    type_name: ClassVar[str] = "kill"
    action_label: ClassVar[str] = "Kill"


@register_event
@dataclass
class AceEvent(ScoringPlayerEvent):
    """An ace: scores a point for the serving team.

    Convention: at creation time, set `team` to the serving team.
    """

    type_name: ClassVar[str] = "ace"
    action_label: ClassVar[str] = "Ace"


@register_event
@dataclass
class AssistEvent(StatPlayerEvent):
    """Assist on a kill (no score change)."""

    type_name: ClassVar[str] = "assist"
    action_label: ClassVar[str] = "Assist"


@register_event
@dataclass
class BlockEvent(StatPlayerEvent):
    """A block (no score change; useful for highlight extraction)."""

    type_name: ClassVar[str] = "block"
    action_label: ClassVar[str] = "Block"


@register_event
@dataclass
class DiveEvent(StatPlayerEvent):
    """A dive."""

    type_name: ClassVar[str] = "dive"
    action_label: ClassVar[str] = "Dive"


@register_event
@dataclass
class DigEvent(StatPlayerEvent):
    """A dig."""

    type_name: ClassVar[str] = "dig"
    action_label: ClassVar[str] = "Dig"


@register_event
@dataclass
class HighlightEvent(StatPlayerEvent):
    """A highlight moment for a player (no score change)."""

    type_name: ClassVar[str] = "highlight"
    action_label: ClassVar[str] = "Highlight"
