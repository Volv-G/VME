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
    # Home jersey numbers designated libero for this match. Liberos may
    # only play the back row; the editor uses this to send one off when
    # a rotation carries them to the front. Per match, not per roster,
    # because the designation changes between matches. The state
    # machine does not read it - the swap is an ordinary substitution
    # event, so a finished match replays identically with or without it.
    liberos: list[int] = field(default_factory=list)
    # Per-match override of how much footage a player-reel segment keeps
    # around a tagged action, in seconds. `None` means "use the defaults"
    # (`render.reels.REEL_MAX_LEAD_SECONDS` / `REEL_MAX_TAIL_SECONDS`) -
    # stored as null rather than a copy of the default so changing the
    # default later actually reaches matches nobody has customized.
    reel_lead_seconds: Optional[float] = None
    reel_tail_seconds: Optional[float] = None
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

    # ---- Absolute time ---------------------------------------------------
    #
    # Everything downstream - renderer, frame map, cuts - works in frames,
    # and still does. These translate between that and wall-clock time so
    # an event tagged somewhere else, against no footage at all, can be
    # placed on this timeline.

    @property
    def timeline_epoch_ms(self) -> Optional[int]:
        """Wall-clock time of global frame 0, in Unix milliseconds.

        Taken from the first clip that knows when it started recording,
        walked back past the clips in front of it. None when no clip has
        a recording time: such a match has no anchor to the world, and
        its events keep frame addressing alone rather than being given
        a fabricated one.
        """
        elapsed_ms = 0.0
        for c in self.clips:
            if c.start_recording_time is not None:
                return int(c.start_recording_time * 1000.0 - elapsed_ms)
            elapsed_ms += c.duration * 1000.0
        return None

    def clip_start_ms(self, clip_id: str) -> Optional[int]:
        """Wall-clock start of a clip.

        Its own recording time when ffprobe found one, otherwise
        projected from the epoch by summing the clips in front of it.
        The projection assumes the clips are contiguous, which is what
        "we did not stop recording between these two" means - and when
        the camera *was* stopped, the clip after the gap almost always
        carries its own timestamp anyway.
        """
        clip = self.get_clip(clip_id)
        if clip is None:
            return None
        if clip.start_recording_time is not None:
            return int(clip.start_recording_time * 1000.0)
        epoch = self.timeline_epoch_ms
        if epoch is None:
            return None
        elapsed_ms = 0.0
        for c in self.clips:
            if c.id == clip_id:
                return int(epoch + elapsed_ms)
            elapsed_ms += c.duration * 1000.0
        return None

    def event_time_ms(self, event: MatchEvent) -> Optional[int]:
        """When an event happened, derived from where it sits."""
        if isinstance(event, ClipTransitionEvent):
            return None
        start = self.clip_start_ms(event.clip_id)
        if start is None:
            return None
        clip = self.get_clip(event.clip_id)
        fps = (clip.fps if clip and clip.fps else self.fps) or 30.0
        return int(start + (event.local_frame / fps) * 1000.0)

    def project_ms(self, at_ms: int) -> Optional[tuple[str, int]]:
        """Frame position for a wall-clock moment.

        A moment inside a clip maps exactly. A moment in a **gap** - the
        camera was stopped, so no footage of it exists - is pulled
        forward to the first frame of the next clip, the earliest place
        it could be shown. Before the first clip is the same case. After
        the last clip lands on its final frame, since there is nothing
        later to pull it to.

        None when the clips carry no recording times at all, because
        then there is no mapping to make.
        """
        if not self.clips:
            return None
        spans: list[tuple[Clip, int, int]] = []
        for c in self.clips:
            start = self.clip_start_ms(c.id)
            if start is None:
                return None
            spans.append((c, start, start + int(c.duration * 1000.0)))

        for clip, start, end in spans:
            if at_ms >= end:
                continue
            if at_ms < start:
                # In the gap before this clip (or before the match).
                return (clip.id, 0)
            fps = (clip.fps or self.fps) or 30.0
            frame = int(round((at_ms - start) / 1000.0 * fps))
            return (clip.id, max(0, min(frame, max(0, clip.frame_count - 1))))

        last = spans[-1][0]
        return (last.id, max(0, last.frame_count - 1))

    def covers_ms(self, at_ms: int) -> bool:
        """True when some clip was recording at that moment.

        The question `project_ms` cannot answer on its own: it always
        returns a position, so the caller cannot tell an exact placement
        from one that was pulled out of a gap. Import reports the
        difference, because "12 of your events happened while the camera
        was stopped" is worth knowing.
        """
        for c in self.clips:
            start = self.clip_start_ms(c.id)
            if start is None:
                continue
            if start <= at_ms < start + int(c.duration * 1000.0):
                return True
        return False

    def backfill_event_times(self) -> bool:
        """Give every event an `at_ms` derived from its frame position.

        Legacy events predate the field. Derived, not guessed: the frame
        position and the clip's recording time between them say exactly
        when it happened. Events on a match whose clips have no recording
        times are left alone - there is nothing to derive from.
        """
        changed = False
        for e in self.events:
            if getattr(e, "at_ms", None) is not None:
                continue
            when = self.event_time_ms(e)
            if when is not None:
                e.at_ms = when
                changed = True
        return changed

    def is_placed(self, event: MatchEvent) -> bool:
        """True when this event sits on a clip that actually exists.

        An event imported before the footage has a time but no position:
        its `clip_id` is empty, so it renders nowhere and seeks nowhere
        until [place_pending_events] finds it a home.
        """
        if isinstance(event, ClipTransitionEvent):
            return True
        return self.get_clip(event.clip_id) is not None

    def place_pending_events(self) -> bool:
        """Give a frame position to events that have a time but no clip.

        Events routinely arrive before the footage does - the phone
        finishes the moment the match does, while the card is still in
        the camera. Rather than refuse the import and make the operator
        remember to come back, such events are stored with their `at_ms`
        alone and placed here, once there is something to place them on.

        Idempotent, and only ever touches events with no valid clip, so
        an event the operator has since nudged by hand is left where
        they put it.
        """
        if not self.clips:
            return False
        changed = False
        for e in self.events:
            if isinstance(e, ClipTransitionEvent):
                continue
            at = getattr(e, "at_ms", None)
            if at is None or self.is_placed(e):
                continue
            placed = self.project_ms(at)
            if placed is not None:
                e.clip_id, e.local_frame = placed
                changed = True
        if changed:
            self._sort_events()
            self._recompute_states()
        return changed

    def reproject_event(self, event: MatchEvent, at_ms: int) -> bool:
        """Move an event to a wall-clock moment, frames and all.

        The single place that keeps the two addressings in step: callers
        that shift an event in time go through here rather than setting
        `at_ms` and leaving the frame position saying something else.
        """
        placed = self.project_ms(at_ms)
        if placed is None:
            return False
        event.clip_id, event.local_frame = placed
        event.at_ms = at_ms
        return True

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
        def key(e: MatchEvent) -> tuple[int, int, int]:
            # Three ranks: everything on the timeline first, ordered by
            # frame; then events that have a time but nowhere to sit yet,
            # ordered by that time. Without the third rank an unplaced
            # log would all collide on one key and keep whatever order it
            # happened to be inserted in - which is right by luck on a
            # fresh import and wrong on the second one.
            if isinstance(e, ClipTransitionEvent):
                # Place at boundary between from_clip and to_clip.
                offset = self.clip_offset(e.from_clip_id)
                clip = self.get_clip(e.from_clip_id)
                if offset is None or clip is None:
                    return (2, 10**9, e.id)
                return (0, offset + clip.frame_count, 0)
            g = self.to_global_frame(e.clip_id, e.local_frame)
            if g is not None:
                return (0, g, 1)
            return (1, getattr(e, "at_ms", None) or 0, e.id)

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
            # `include_admin=False`: opponent's youtube/naming
            # settings are meaningless (the opponent isn't uploading
            # to your channel and doesn't influence your file
            # naming). Strips those keys so match.json stays clean.
            "opponent_roster": self.opponent_roster.to_dict(include_admin=False),
            "liberos": list(self.liberos),
            "reel_lead_seconds": self.reel_lead_seconds,
            "reel_tail_seconds": self.reel_tail_seconds,
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
            liberos=[int(n) for n in (data.get("liberos") or [])],
            reel_lead_seconds=_opt_float(data.get("reel_lead_seconds")),
            reel_tail_seconds=_opt_float(data.get("reel_tail_seconds")),
            schema_version=int(data.get("schema_version", SCHEMA_VERSION)),
        )
        m._sort_events()
        # Legacy matches gain their wall-clock times the first time they
        # are read. Only fills what is missing, so an event that already
        # carries one is never second-guessed.
        m.backfill_event_times()
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


def _opt_float(value: Any) -> Optional[float]:
    """Float, or None for null / blank / unparseable.

    Tolerant on purpose: these fields are hand-editable in match.json,
    and a typo there should fall back to the default rather than make
    the whole match unloadable.
    """
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        logger.warning("ignoring non-numeric reel padding value %r", value)
        return None


def _event_references_clip(event: MatchEvent, clip_id: str) -> bool:
    if isinstance(event, ClipTransitionEvent):
        return event.from_clip_id == clip_id or event.to_clip_id == clip_id
    return event.clip_id == clip_id


def new_clip_id() -> str:
    return uuid.uuid4().hex[:8]
