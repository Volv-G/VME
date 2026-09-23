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
from ..game_state import GameState
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

    def apply(self, state: GameState, all_events):
        # Capture the OUTGOING player from state-before so the popup can
        # display both jerseys (in → out). The outgoing number is a
        # transient attribute - not serialized, recomputed every time
        # `_recompute_states` runs - so it's always consistent with the
        # current event ordering even after reorders / inserts.
        positions = (
            state.home_positions if self.team == Team.HOME else state.away_positions
        )
        self._player_out_number = positions.get(self.position)
        return super().apply(state, all_events)

    @property
    def overlay_effect(self) -> Optional[OverlayEffect]:
        # No `title_scale` override needed - the renderer's global
        # TITLE_FONT_SCALE now matches what we previously hand-tuned
        # here (small bold title, larger-feeling subtitle below).
        out_number = getattr(self, "_player_out_number", None)
        # A slot that was empty - a placeholder in the line-up, or the
        # line-up being filled in at set start - has nobody to swap
        # out, so "Sub" describes something that did not happen: the
        # player is taking the court, not replacing anyone. The
        # subtitle already drops the "out -> in" arrow in this case.
        # The phone's live overlay has always said "Entering" here;
        # this is VME catching up with it.
        return PlayerPopupEffect(
            text="Sub" if out_number is not None else "Entering",
            player_number=self.player_in_number,
            player_out_number=out_number,
            team=self.team,
        )
