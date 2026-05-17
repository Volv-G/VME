"""Multi-clip frame map.

Each output frame has:
  - One or more `inputs` referencing a (clip_id, local_frame) and a blend weight
  - A `color_weight` in [0, 1] indicating how much to blend toward `blend_color`
    (0 = full source, 1 = full color)
  - Pre-computed overlay state for the renderer
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class FrameInput:
    clip_id: str
    local_frame: int
    weight: float = 1.0


@dataclass
class ActiveMessage:
    """A single popup overlay active on this frame."""

    text: str
    image_path: Optional[str]
    bg_color: Optional[str]
    progress: float  # 0.0 = just appeared, 1.0 = about to disappear
    # When set, the renderer will look up the player by jersey number on
    # `team` ("home"/"away") and append the name as a subtitle. The team
    # color (from its roster) is used as the background unless `bg_color`
    # provides an explicit override.
    team: Optional[str] = None
    player_number: Optional[int] = None


@dataclass
class FrameEntry:
    inputs: list[FrameInput] = field(default_factory=list)
    color_weight: float = 0.0
    blend_color: tuple[int, int, int] = (0, 0, 0)
    scoreboard_visible: bool = False
    messages: list[ActiveMessage] = field(default_factory=list)


class FrameMap:
    """Ordered sequence of output frame entries."""

    def __init__(self, fps: float = 30.0) -> None:
        self.fps = fps
        self._entries: list[FrameEntry] = []

    def __len__(self) -> int:
        return len(self._entries)

    def __getitem__(self, idx: int) -> FrameEntry:
        return self._entries[idx]

    def __iter__(self):
        return iter(self._entries)

    def append(self, entry: FrameEntry) -> None:
        self._entries.append(entry)

    @property
    def entries(self) -> list[FrameEntry]:
        return self._entries

    def duration_seconds(self) -> float:
        return len(self._entries) / self.fps if self.fps else 0.0
