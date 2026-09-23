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


# Matches the editor's Cut End button, so a timeout joins the footage
# the way a cut the user placed by hand would: half a second of
# crossfade at 60 fps rather than a hard jump from the huddle to a
# serve. Frames rather than seconds because that is what every cut end
# stores; the event form shows both.
TIMEOUT_FADE_FRAMES = 30


@register_event
@dataclass
class TimeoutStartEvent(CutStartEvent):
    """A timeout was called. Tagged live, on the phone.

    Subclasses `CutStartEvent` because, for now, a timeout is exactly a
    cut: the span to its `TimeoutEndEvent` is dropped from the render.
    Every consumer of cuts - the frame map, highlight rally bounds, the
    auto-cut idempotency check - asks `isinstance`, so a timeout is
    treated as a cut everywhere without any of them knowing timeouts
    exist. When a timeout should do something a cut does not (a
    "TIMEOUT" card instead of dropping the footage, say), this stops
    being a subclass and those call sites choose.

    A start with no end - the operator never closed it - behaves like
    any unpaired cut start: it pairs with a set/game end only if no
    serve comes first, so a forgotten timeout cannot eat a rally.
    """

    type_name: ClassVar[str] = "timeout_start"


@register_event
@dataclass
class TimeoutEndEvent(CutEndEvent):
    """Play resumed after a timeout. Closes the cut its start opened.

    The phone logs this when the operator ends the timeout, or - if
    they go straight to Ball Served - just ahead of that serve, at the
    same instant.
    """

    type_name: ClassVar[str] = "timeout_end"

    fade_frames: int = TIMEOUT_FADE_FRAMES
    frame_shift: int = -TIMEOUT_FADE_FRAMES


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
