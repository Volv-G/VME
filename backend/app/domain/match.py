"""Match: clips + events + computed state.

Frame storage is clip-relative (each event has `clip_id` + `local_frame`),
so reordering, inserting, or deleting clips never requires touching events.
The `Match` resolves to a global frame index whenever the renderer needs it.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from .events import event_from_dict, event_to_dict
from .events.base import MatchEvent, ValidationError
from .events.timeline import ClipTransitionEvent
from .game_state import GameState, ValidationState
from .roster import Roster

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1


@dataclass
class Clip:
    """A single source video file referenced by a match."""

    id: str
    filename: str
    frame_count: int
    fps: float = 30.0
    width: int = 0
    height: int = 0
    # Unix timestamp (UTC) when the camera started recording this file.
    # Populated from ffprobe's `creation_time` tag (or filesystem mtime -
    # duration as a fallback). Used by the auto-cuts heuristic to detect
    # back-to-back recordings.
    start_recording_time: Optional[float] = None

    @property
    def duration(self) -> float:
        return self.frame_count / self.fps if self.fps else 0.0

    @property
    def end_recording_time(self) -> Optional[float]:
        if self.start_recording_time is None:
            return None
        return self.start_recording_time + self.duration

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "filename": self.filename,
            "frame_count": self.frame_count,
            "fps": self.fps,
            "width": self.width,
            "height": self.height,
            "start_recording_time": self.start_recording_time,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Clip":
        srt = data.get("start_recording_time")
        return cls(
            id=str(data.get("id") or uuid.uuid4().hex[:8]),
            filename=data["filename"],
            frame_count=int(data.get("frame_count", 0)),
            fps=float(data.get("fps", 30.0)),
            width=int(data.get("width", 0)),
            height=int(data.get("height", 0)),
            start_recording_time=float(srt) if srt is not None else None,
        )


@dataclass
class Match:
    """A volleyball match: clip list (ordered) + events + opponent metadata."""

    opponent: str = ""
    date: str = ""
    fps: float = 30.0
    clips: list[Clip] = field(default_factory=list)
    events: list[MatchEvent] = field(default_factory=list)
    opponent_roster: Roster = field(default_factory=Roster)
    schema_version: int = SCHEMA_VERSION

    # ---- Clip operations -------------------------------------------------

    def add_clip(self, clip: Clip, index: Optional[int] = None) -> None:
        if index is None or index >= len(self.clips):
            self.clips.append(clip)
        else:
            self.clips.insert(index, clip)

    def remove_clip(self, clip_id: str) -> None:
        self.clips = [c for c in self.clips if c.id != clip_id]
        self.events = [e for e in self.events if not _event_references_clip(e, clip_id)]

    def reorder_clips(self, ordered_ids: list[str]) -> None:
        by_id = {c.id: c for c in self.clips}
        if set(ordered_ids) != set(by_id.keys()):
            raise ValueError("Reorder list must contain exactly the existing clip ids")
        self.clips = [by_id[cid] for cid in ordered_ids]

    def get_clip(self, clip_id: str) -> Optional[Clip]:
        for c in self.clips:
            if c.id == clip_id:
                return c
        return None

    def total_frames(self) -> int:
        return sum(c.frame_count for c in self.clips)

    # ---- Frame index resolution -----------------------------------------

    def clip_offset(self, clip_id: str) -> Optional[int]:
        offset = 0
        for c in self.clips:
            if c.id == clip_id:
                return offset
            offset += c.frame_count
        return None

    def to_global_frame(self, clip_id: str, local_frame: int) -> Optional[int]:
        offset = self.clip_offset(clip_id)
        if offset is None:
            return None
        return offset + local_frame

    def from_global_frame(self, global_frame: int) -> Optional[tuple[str, int]]:
        offset = 0
        for c in self.clips:
            if global_frame < offset + c.frame_count:
                return (c.id, global_frame - offset)
            offset += c.frame_count
        return None

    # ---- Events ----------------------------------------------------------

    def add_event(self, event: MatchEvent) -> int:
        event.id = self._next_event_id()
        self.events.append(event)
        self._sort_events()
        self._recompute_states()
        return event.id

    def update_event(self, event_id: int, updates: dict[str, Any]) -> Optional[MatchEvent]:
        for e in self.events:
            if e.id == event_id:
                for k, v in updates.items():
                    if hasattr(e, k):
                        setattr(e, k, v)
                self._sort_events()
                self._recompute_states()
                return e
        return None

    def delete_event(self, event_id: int) -> None:
        self.events = [e for e in self.events if e.id != event_id]
        self._recompute_states()

    def get_event(self, event_id: int) -> Optional[MatchEvent]:
        for e in self.events:
            if e.id == event_id:
                return e
        return None

    def events_at_global_frame(self, global_frame: int) -> list[MatchEvent]:
        return [
            e
            for e in self.events
            if not isinstance(e, ClipTransitionEvent)
            and self.to_global_frame(e.clip_id, e.local_frame) == global_frame
        ]

    def state_at_global_frame(self, global_frame: int) -> GameState:
        """Return the state after the last event at or before `global_frame`."""
        state = GameState()
        for e in self.events:
            if isinstance(e, ClipTransitionEvent):
                continue
            g = self.to_global_frame(e.clip_id, e.local_frame)
            if g is None or g > global_frame:
                continue
            if e.state is not None:
                state = e.state
        return state

    def validate(self) -> list[ValidationError]:
        """Re-run validation across all events and return a flat list."""
        errors: list[ValidationError] = []
        state = GameState()
        for e in self.events:
            if isinstance(e, ClipTransitionEvent):
                continue
            errors.extend(e.validate(state, self.events))
            state = e.apply(state, self.events)
        return errors

    # ---- Internals --------------------------------------------------------

    def _next_event_id(self) -> int:
        return max((e.id for e in self.events), default=0) + 1

    def _sort_events(self) -> None:
        def key(e: MatchEvent) -> tuple[int, int]:
            if isinstance(e, ClipTransitionEvent):
                # Place at boundary between from_clip and to_clip.
                offset = self.clip_offset(e.from_clip_id)
                clip = self.get_clip(e.from_clip_id)
                if offset is None or clip is None:
                    return (10**9, e.id)
                return (offset + clip.frame_count, 0)
            g = self.to_global_frame(e.clip_id, e.local_frame)
            return (g if g is not None else 10**9, 1)

        self.events.sort(key=key)

    def _recompute_states(self) -> None:
        state = GameState()
        for e in self.events:
            if isinstance(e, ClipTransitionEvent):
                # Transitions don't affect state.
                e.state = state
                continue
            state = e.apply(state, self.events)
            e.state = state

    # ---- Persistence ------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "opponent": self.opponent,
            "date": self.date,
            "fps": self.fps,
            "clips": [c.to_dict() for c in self.clips],
            "events": [event_to_dict(e) for e in self.events],
            "opponent_roster": self.opponent_roster.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Match":
        m = cls(
            opponent=data.get("opponent", ""),
            date=data.get("date", ""),
            fps=float(data.get("fps", 30.0)),
            clips=[Clip.from_dict(c) for c in data.get("clips", [])],
            events=[event_from_dict(e) for e in data.get("events", [])],
            opponent_roster=Roster.from_dict(data.get("opponent_roster", {})),
            schema_version=int(data.get("schema_version", SCHEMA_VERSION)),
        )
        m._sort_events()
        m._recompute_states()
        return m

    @classmethod
    def load(cls, path: str | Path) -> "Match":
        with open(path, "r", encoding="utf-8") as f:
            return cls.from_dict(json.load(f))

    def save(self, path: str | Path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)


def _event_references_clip(event: MatchEvent, clip_id: str) -> bool:
    if isinstance(event, ClipTransitionEvent):
        return event.from_clip_id == clip_id or event.to_clip_id == clip_id
    return event.clip_id == clip_id


def new_clip_id() -> str:
    return uuid.uuid4().hex[:8]
