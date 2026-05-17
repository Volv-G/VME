"""Roster events: substitutions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Optional

from ..effects import (
    OverlayEffect,
    PlayerPopupEffect,
    RosterEffect,
    SubstitutionApplyEffect,
)
from ..team import Team
from .base import MatchEvent
from .registry import register_event


@register_event
@dataclass
class SubstitutionEvent(MatchEvent):
    """A substitution: `player_in_number` enters at `position` for `team`."""

    type_name: ClassVar[str] = "substitution"

    team: Team = Team.HOME
    position: int = 1
    player_in_number: int = 0

    @property
    def roster_effect(self) -> Optional[RosterEffect]:
        return SubstitutionApplyEffect(
            team=self.team,
            position=self.position,
            player_in=self.player_in_number,
        )

    @property
    def overlay_effect(self) -> Optional[OverlayEffect]:
        return PlayerPopupEffect(
            text="Sub",
            player_number=self.player_in_number,
            team=self.team,
        )
