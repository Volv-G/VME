"""Focus events: mark spans where a single player is highlighted."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from ..team import Team
from .base import MatchEvent
from .registry import register_event


@register_event
@dataclass
class FocusInEvent(MatchEvent):
    """Begin a focus segment on a player."""

    type_name: ClassVar[str] = "focus_in"

    team: Team = Team.HOME
    player_number: int = 0


@register_event
@dataclass
class FocusOutEvent(MatchEvent):
    """End a focus segment."""

    type_name: ClassVar[str] = "focus_out"
