"""Timeline events: cuts and clip transitions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, Optional

from ..effects import TimelineEffect
from .base import MatchEvent
from .registry import register_event


@register_event
@dataclass
class CutStartEvent(MatchEvent):
    """Marks the start of a region whose frames are dropped from the render.

    Pairs with a `CutEndEvent` in the same clip.
    """

    type_name: ClassVar[str] = "cut_start"


@register_event
@dataclass
class CutEndEvent(MatchEvent):
    """Marks the end of a cut region; controls how the rejoin is rendered.

    `frame_shift` of `-fade_frames` produces a crossfade.
    `frame_shift` of 0 with non-zero `fade_frames` produces a fade-to-color hold.
    """

    type_name: ClassVar[str] = "cut_end"

    fade_frames: int = 0
    frame_shift: int = 0
    blend_color: tuple[int, int, int] = (0, 0, 0)

    @property
    def timeline_effect(self) -> Optional[TimelineEffect]:
        return TimelineEffect(
            fade_frames=self.fade_frames,
            frame_shift=self.frame_shift,
            blend_color=self.blend_color,
        )


@register_event
@dataclass
class ClipTransitionEvent(MatchEvent):
    """Transition between two adjacent clips (no frames removed).

    Lives at a boundary, not at a frame: `clip_id` and `local_frame` are unused.
    The renderer places the effect at the join between `from_clip_id` and
    `to_clip_id` in the `Match.clips` order.
    """

    type_name: ClassVar[str] = "clip_transition"

    from_clip_id: str = ""
    to_clip_id: str = ""
    fade_frames: int = 0
    frame_shift: int = 0
    blend_color: tuple[int, int, int] = (0, 0, 0)

    @property
    def timeline_effect(self) -> Optional[TimelineEffect]:
        return TimelineEffect(
            fade_frames=self.fade_frames,
            frame_shift=self.frame_shift,
            blend_color=self.blend_color,
        )
