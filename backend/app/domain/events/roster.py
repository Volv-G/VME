"""Roster events: substitutions, the libero's 3-way rotation, and who
the liberos are."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Optional, Tuple

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


@register_event
@dataclass
class RotationEvent(MatchEvent):
    """A rotation the rules did not make: the scorer correcting the line-up.

    The rotation normally comes from the score - a side-out turns the
    team that won it - so a missed point, a rally scored to the wrong
    side or a referee's rotation call leaves the line-up a slot out with
    no event to fix it but six substitutions. This turns it `steps`
    slots: +1 forward (clockwise, as on a side-out), -1 back. Puts
    nothing on the video.
    """

    type_name: ClassVar[str] = "rotation"

    team: Team = Team.HOME
    steps: int = 1

    def apply(self, state: GameState, all_events):
        new_state = super().apply(state, all_events)
        # -1 is five turns forward: six turns is the same line-up.
        for _ in range(self.steps % 6):
            new_state.rotate_team(self.team)
        return new_state


@register_event
@dataclass
class LiberosEvent(MatchEvent):
    """From here on, these home jerseys are the liberos.

    A team may name different liberos for each set - the second libero
    starting the next one, a player swapping into the libero shirt - so
    the designation is an event, placed where it changes (typically
    between a set end and the next first serve), rather than one list
    for the match. Before the first of these, the match's own list
    (`Match.liberos`) applies, so matches set up with one keep it.

    Changes nothing on court and puts nothing on the video: it changes
    who the libero rules - back row only, replacement at the rotation -
    apply to. An empty `numbers` means no liberos from here.
    """

    type_name: ClassVar[str] = "liberos"

    numbers: Tuple[int, ...] = ()

    def apply(self, state: GameState, all_events):
        new_state = super().apply(state, all_events)
        new_state.liberos = list(dict.fromkeys(int(n) for n in self.numbers))
        return new_state


@register_event
@dataclass
class LiberoRotationEvent(MatchEvent):
    """The libero's swap at a rotation, as the one event it is.

    When the rotation carries the libero to the front row they come off
    at `exit_position` for the player they were covering
    (`player_in_number`), and in the same stoppage go back on at
    `entry_position` for whoever has just rotated to the back row. That
    used to be two substitutions - `Libero -> #12`, `#9 -> Libero` -
    logged seconds apart by the phone (the first automatically when the
    point is scored, the second by the operator), so the video showed two
    "Sub" pop-ups at two moments for one thing happening once. The
    editor collapses such a pair into this (`POST /events/libero-rotation`).

    Applies both moves at once; the player going off is read from the
    state before it, like `SubstitutionEvent`'s outgoing player.
    """

    type_name: ClassVar[str] = "libero_rotation"

    team: Team = Team.HOME
    libero_number: int = 0
    # Defaults of 0, which is never a position, so both are always
    # written out: fields equal to their default are omitted from the
    # payload, and readers would otherwise have to know what 4 and 1
    # meant by their absence.
    exit_position: int = 0
    player_in_number: int = 0
    entry_position: int = 0

    def apply(self, state: GameState, all_events):
        positions = (
            state.home_positions if self.team == Team.HOME else state.away_positions
        )
        self._player_out_number = positions.get(self.entry_position)
        new_state = super().apply(state, all_events)
        new_state.set_position(self.team, self.exit_position, self.player_in_number)
        new_state.set_position(self.team, self.entry_position, self.libero_number)
        new_state.last_substitution_frame = self.local_frame
        return new_state

    @property
    def overlay_effect(self) -> Optional[OverlayEffect]:
        return PlayerPopupEffect(
            text="Libero rotation",
            player_number=self.player_in_number,
            player_out_number=getattr(self, "_player_out_number", None),
            team=self.team,
            kind="libero_rotation",
        )
