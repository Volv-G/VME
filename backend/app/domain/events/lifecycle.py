"""Game lifecycle events: GameStart, GameEnd, SetEnd."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Optional

from ..effects import (
    PositionsResetEffect,
    RosterEffect,
    ScoreEffect,
    ScoreResetEffect,
    ScoreboardVisibilityEffect,
    OverlayEffect,
    TimelineEffect,
)
from ..game_state import GameState
from .base import MatchEvent
from .registry import register_event


@dataclass
class LifecycleEvent(MatchEvent):
    """Base for events that mark a phase change in the match.

    Lifecycle events have a `fade_frames` parameter used for fading to/from
    `blend_color`; `frame_shift` is always 0 (lifecycle events do not overlap
    sections).

    The default is 60 frames - one second at 60fps, matching the bookend
    fades at the very start and end of a render. It also matters more than
    it used to: when a lifecycle event closes an open cut, this fade is the
    whole transition across the removed footage, so half a second read as
    an abrupt blink rather than a set break.

    Events that stored an explicit value keep it; only ones saved with the
    old default change.
    """

    fade_frames: int = 60
    blend_color: tuple[int, int, int] = (0, 0, 0)

    @property
    def timeline_effect(self) -> Optional[TimelineEffect]:
        if self.fade_frames <= 0:
            return None
        return TimelineEffect(
            fade_frames=self.fade_frames,
            frame_shift=0,
            blend_color=self.blend_color,
        )


@register_event
@dataclass
class GameStartEvent(LifecycleEvent):
    """Game begins: scoreboard becomes visible; fade-in from `blend_color`."""

    type_name: ClassVar[str] = "game_start"

    @property
    def overlay_effect(self) -> Optional[OverlayEffect]:
        return ScoreboardVisibilityEffect(visible=True)

    def apply(self, state: GameState, all_events):
        new_state = super().apply(state, all_events)
        new_state.game_started = True
        new_state.game_ended = False
        return new_state


@register_event
@dataclass
class GameEndEvent(LifecycleEvent):
    """Game ends: scoreboard hides; fade-out to `blend_color`."""

    type_name: ClassVar[str] = "game_end"

    @property
    def overlay_effect(self) -> Optional[OverlayEffect]:
        return ScoreboardVisibilityEffect(visible=False)

    def apply(self, state: GameState, all_events):
        new_state = super().apply(state, all_events)
        new_state.game_ended = True
        return new_state


@register_event
@dataclass
class SetEndEvent(LifecycleEvent):
    """End of a set: increment winner's set count, reset scores+positions."""

    type_name: ClassVar[str] = "set_end"

    @property
    def score_effect(self) -> Optional[ScoreEffect]:
        return ScoreResetEffect()

    @property
    def roster_effect(self) -> Optional[RosterEffect]:
        return PositionsResetEffect()
