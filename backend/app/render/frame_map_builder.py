"""Build a `FrameMap` from a `Match` by consuming event effect properties.

The builder makes no decisions based on event class names (other than
`ClipTransitionEvent`, which is structurally special because it lives at a
clip boundary). All transition behavior comes from `TimelineEffect`, all
overlay behavior from `OverlayEffect`.

Pipeline:
    1. Concatenate clips, skipping frames inside `CutStart..CutEnd` regions.
       Each output frame gets a single input.
    2. For each event with a `TimelineEffect`, locate its join point in output
       coordinates and apply `fade_frames`, `frame_shift`, and `blend_color`
       to the frames around it.
    3. Walk events in order to populate per-frame overlay state
       (scoreboard visibility, active popup messages).
    4. Apply bookend fade-in / fade-out so the output always starts and ends
       on `blend_color`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from ..domain.effects import (
    MessagePopupEffect,
    PlayerPopupEffect,
    ScoreboardVisibilityEffect,
    TimelineEffect,
)
from ..domain.events.base import MatchEvent
from ..domain.events.timeline import (
    ClipTransitionEvent,
    CutEndEvent,
    CutStartEvent,
)
from ..domain.match import Match
from .frame_map import ActiveMessage, FrameEntry, FrameInput, FrameMap

logger = logging.getLogger(__name__)


@dataclass
class _CutRegion:
    """A frame range to be removed, with its closing event."""

    clip_id: str
    start_local: int  # inclusive
    end_local: int  # exclusive
    end_event: Optional[CutEndEvent]


def build_frame_map(
    match: Match,
    *,
    bookend_fade_seconds: float = 1.0,
    popup_duration_seconds: float = 3.0,
) -> FrameMap:
    fps = match.fps or 30.0
    fmap = FrameMap(fps=fps)

    cut_regions = _collect_cut_regions(match)
    pass1_concat(match, fmap, cut_regions)
    apply_timeline_effects(match, fmap, cut_regions)
    apply_overlay_state(match, fmap, popup_duration_seconds=popup_duration_seconds)
    apply_bookend_fades(fmap, fade_frames=int(bookend_fade_seconds * fps))

    return fmap


# ----- Pass 1: concatenate, drop cut regions ----------------------------------


def _collect_cut_regions(match: Match) -> list[_CutRegion]:
    """Pair cut_start <-> cut_end events globally; cuts may span clips.

    A cut that opens in clip A and closes in clip C produces one `_CutRegion`
    per affected clip: a tail sub-region in A, the full extent of any clips
    between A and C, and a head sub-region in C. The closing CutEnd event is
    attached to the LAST sub-region so timeline-effect handling still finds
    it via the event's own `clip_id` + `local_frame`.

    Orphans (an unmatched CutStart, or a CutEnd with nothing open) are simply
    ignored here; the UI surfaces them in red so the user can fix them.
    """
    # Build a flat list of (global_frame, clip_idx, local_frame, event)
    # for cut_start / cut_end events, sorted in timeline order.
    by_id: dict[str, int] = {c.id: i for i, c in enumerate(match.clips)}
    sortable: list[tuple[int, int, int, MatchEvent]] = []
    for e in match.events:
        if isinstance(e, ClipTransitionEvent):
            continue
        if not isinstance(e, (CutStartEvent, CutEndEvent)):
            continue
        idx = by_id.get(e.clip_id)
        if idx is None:
            continue
        g = match.to_global_frame(e.clip_id, e.local_frame)
        if g is None:
            continue
        sortable.append((g, idx, e.local_frame, e))
    sortable.sort(key=lambda t: t[0])

    regions: list[_CutRegion] = []
    open_event: Optional[tuple[int, int]] = None  # (clip_idx, local_frame)

    for _, idx, local_frame, ev in sortable:
        if isinstance(ev, CutStartEvent):
            if open_event is None:
                open_event = (idx, local_frame)
            # else: nested CutStart; treated as orphan by the UI.
        else:  # CutEndEvent
            if open_event is None:
                continue  # orphan CutEnd; surfaced by UI.
            start_idx, start_local = open_event
            end_idx, end_local = idx, local_frame
            regions.extend(
                _build_cross_clip_regions(
                    match, start_idx, start_local, end_idx, end_local, ev
                )
            )
            open_event = None

    return regions


def _build_cross_clip_regions(
    match: Match,
    start_idx: int,
    start_local: int,
    end_idx: int,
    end_local: int,
    end_event: CutEndEvent,
) -> list[_CutRegion]:
    """Slice a possibly-cross-clip cut into per-clip `_CutRegion`s.

    `end_event` is attached to the LAST sub-region only.
    """
    out: list[_CutRegion] = []
    if start_idx == end_idx:
        clip = match.clips[start_idx]
        out.append(
            _CutRegion(
                clip_id=clip.id,
                start_local=max(0, start_local),
                end_local=min(clip.frame_count, end_local),
                end_event=end_event,
            )
        )
        return out

    # Tail of the starting clip: [start_local, frame_count).
    start_clip = match.clips[start_idx]
    out.append(
        _CutRegion(
            clip_id=start_clip.id,
            start_local=max(0, start_local),
            end_local=start_clip.frame_count,
            end_event=None,
        )
    )
    # Entire body of any clips between start and end.
    for idx in range(start_idx + 1, end_idx):
        mid = match.clips[idx]
        out.append(
            _CutRegion(
                clip_id=mid.id,
                start_local=0,
                end_local=mid.frame_count,
                end_event=None,
            )
        )
    # Head of the ending clip: [0, end_local). Carries the CutEnd event.
    end_clip = match.clips[end_idx]
    out.append(
        _CutRegion(
            clip_id=end_clip.id,
            start_local=0,
            end_local=min(end_clip.frame_count, end_local),
            end_event=end_event,
        )
    )
    return out


def _is_in_cut(clip_id: str, local_frame: int, regions: list[_CutRegion]) -> bool:
    for r in regions:
        if r.clip_id == clip_id and r.start_local <= local_frame < r.end_local:
            return True
    return False


def pass1_concat(match: Match, fmap: FrameMap, cut_regions: list[_CutRegion]) -> None:
    for clip in match.clips:
        for f in range(clip.frame_count):
            if _is_in_cut(clip.id, f, cut_regions):
                continue
            entry = FrameEntry(inputs=[FrameInput(clip_id=clip.id, local_frame=f, weight=1.0)])
            fmap.append(entry)


# ----- Pass 2: timeline effects (cut/transition fades + crossfades) -----------


def _output_index_for_input(
    fmap: FrameMap, clip_id: str, local_frame: int
) -> Optional[int]:
    """Find the output frame index whose primary input is (clip_id, local_frame).

    Linear scan; called O(events) times so total cost stays linear.
    """
    for i, entry in enumerate(fmap.entries):
        if not entry.inputs:
            continue
        inp = entry.inputs[0]
        if inp.clip_id == clip_id and inp.local_frame == local_frame:
            return i
    return None


def _output_index_at_clip_boundary(fmap: FrameMap, from_clip_id: str) -> Optional[int]:
    """Return output index of the last frame from `from_clip_id`, +1.

    That is the index where the *next* clip's frames begin (the join).
    """
    last_match = -1
    for i, entry in enumerate(fmap.entries):
        if entry.inputs and entry.inputs[0].clip_id == from_clip_id:
            last_match = i
    return last_match + 1 if last_match >= 0 else None


def apply_timeline_effects(
    match: Match, fmap: FrameMap, cut_regions: list[_CutRegion]
) -> None:
    """Apply each event's `TimelineEffect` (fade_frames, frame_shift, blend_color)."""

    for event in match.events:
        effect = event.timeline_effect
        if effect is None:
            continue

        join = _resolve_join(match, fmap, event, cut_regions)
        if join is None:
            continue

        _apply_effect_at_join(fmap, join, effect)


def _resolve_join(
    match: Match,
    fmap: FrameMap,
    event: MatchEvent,
    cut_regions: list[_CutRegion],
) -> Optional[int]:
    """Return the output index where `event`'s transition is centered.

    For a `CutEndEvent`, that's the first output frame of section B (the frame
    right after the cut).
    For a `ClipTransitionEvent`, that's the first output frame of `to_clip_id`.
    For lifecycle events, that's the output frame containing the event itself.
    """
    if isinstance(event, ClipTransitionEvent):
        return _output_index_at_clip_boundary(fmap, event.from_clip_id)

    if isinstance(event, CutEndEvent):
        # First non-cut frame at or after event.local_frame in the same clip.
        clip = match.get_clip(event.clip_id)
        if clip is None:
            return None
        for f in range(event.local_frame, clip.frame_count):
            if not _is_in_cut(event.clip_id, f, cut_regions):
                return _output_index_for_input(fmap, event.clip_id, f)
        return None

    # Lifecycle (GameStart/End, SetEnd) and any other event.
    return _output_index_for_input(fmap, event.clip_id, event.local_frame)


def _apply_effect_at_join(
    fmap: FrameMap, join: int, effect: TimelineEffect
) -> None:
    """Apply `effect` so that the join sits between sections A and B.

    Section A is `fmap.entries[:join]`; section B is `fmap.entries[join:]`.

    `fade_frames` (F) and `frame_shift` (S) jointly define:
      - A's tail of length F ramps `color_weight` 0->1 (toward `blend_color`).
      - B's head of length F ramps `color_weight` 1->0 (back from color).
      - If S < 0, B is shifted left by |S| frames so its head overlaps A's tail.
        In the overlap, that frame has TWO inputs (A and B) with crossfade weights;
        `color_weight` is set to 0 (sources cover each other, color hides nothing).
    """
    F = max(0, effect.fade_frames)
    S = effect.frame_shift
    color = effect.blend_color
    if F == 0 and S == 0:
        return

    n = len(fmap)
    if join <= 0 or join >= n:
        return

    overlap = max(0, -S)
    overlap = min(overlap, F, join, n - join)

    # 2a) Pure-color fade on A's tail (the part NOT overlapped by B).
    a_color_len = F - overlap
    a_color_start = max(0, join - F)
    for i in range(a_color_start, a_color_start + a_color_len):
        if i < 0 or i >= n:
            continue
        progress = (i - a_color_start + 1) / max(1, a_color_len)
        _set_color_blend(fmap[i], progress, color)

    # 2b) Pure-color fade on B's head (the part NOT overlapped by A).
    b_color_len = F - overlap
    b_color_start = join + overlap
    for k in range(b_color_len):
        i = b_color_start + k
        if i < 0 or i >= n:
            continue
        progress = 1.0 - (k + 1) / max(1, b_color_len)
        _set_color_blend(fmap[i], progress, color)

    # 2c) Overlap region: cross-blend A's tail and B's head into the same output frames.
    if overlap > 0:
        # B's head currently lives at indices [join..join+overlap). Re-seat it onto
        # indices [join-overlap..join) by combining inputs with A's tail.
        for k in range(overlap):
            a_idx = join - overlap + k
            b_idx = join + k
            if a_idx < 0 or b_idx >= n:
                continue
            t = (k + 1) / (overlap + 1)
            a_inputs = [
                FrameInput(inp.clip_id, inp.local_frame, inp.weight * (1.0 - t))
                for inp in fmap[a_idx].inputs
            ]
            b_inputs = [
                FrameInput(inp.clip_id, inp.local_frame, inp.weight * t)
                for inp in fmap[b_idx].inputs
            ]
            merged = a_inputs + b_inputs
            fmap[a_idx].inputs = merged
            fmap[a_idx].color_weight = 0.0

        # Mark the overlap-consumed B frames as "skip" by zeroing their weights
        # but leaving them in the map. We need to actually remove them so output
        # length matches expected duration; do it now.
        del fmap.entries[join:join + overlap]


def _set_color_blend(
    entry: FrameEntry, weight: float, color: tuple[int, int, int]
) -> None:
    """Set `color_weight` to the max of existing and `weight`; remember the color
    that produced the strongest blend.
    """
    if weight > entry.color_weight:
        entry.color_weight = weight
        entry.blend_color = color


# ----- Pass 3: per-frame overlay state ---------------------------------------


def apply_overlay_state(
    match: Match,
    fmap: FrameMap,
    *,
    popup_duration_seconds: float,
) -> None:
    fps = fmap.fps
    popup_frames = max(1, int(popup_duration_seconds * fps))

    # Build event list with output indices for non-transition events.
    indexed: list[tuple[int, MatchEvent]] = []
    for e in match.events:
        if isinstance(e, ClipTransitionEvent):
            continue
        out_idx = _output_index_for_input(fmap, e.clip_id, e.local_frame)
        if out_idx is not None:
            indexed.append((out_idx, e))
    indexed.sort(key=lambda p: p[0])

    scoreboard_visible = False
    active_popups: list[tuple[int, MatchEvent]] = []  # (start_idx, event)

    n = len(fmap)
    j = 0
    for i in range(n):
        # Apply any events at this frame.
        while j < len(indexed) and indexed[j][0] == i:
            _, ev = indexed[j]
            j += 1
            ovr = ev.overlay_effect
            if isinstance(ovr, ScoreboardVisibilityEffect):
                scoreboard_visible = ovr.visible
            elif isinstance(ovr, (PlayerPopupEffect, MessagePopupEffect)):
                active_popups.append((i, ev))

        fmap[i].scoreboard_visible = scoreboard_visible

        # Emit active popups, dropping any that have aged out.
        still_active: list[tuple[int, MatchEvent]] = []
        for start_idx, ev in active_popups:
            age = i - start_idx
            if age >= popup_frames:
                continue
            still_active.append((start_idx, ev))
            ovr = ev.overlay_effect
            if isinstance(ovr, PlayerPopupEffect):
                fmap[i].messages.append(
                    ActiveMessage(
                        text=ovr.text,
                        image_path=ovr.image_path,
                        bg_color=ovr.bg_color,
                        progress=age / popup_frames,
                        team=ovr.team.value if ovr.team is not None else None,
                        player_number=ovr.player_number,
                    )
                )
            elif isinstance(ovr, MessagePopupEffect):
                fmap[i].messages.append(
                    ActiveMessage(
                        text=ovr.text,
                        image_path=ovr.image_path,
                        bg_color=None,
                        progress=age / popup_frames,
                    )
                )
        active_popups = still_active


# ----- Pass 4: bookend fades -------------------------------------------------


def apply_bookend_fades(fmap: FrameMap, fade_frames: int) -> None:
    if fade_frames <= 0 or len(fmap) == 0:
        return
    n = len(fmap)
    for i in range(min(fade_frames, n)):
        progress = 1.0 - (i + 1) / fade_frames
        if progress > fmap[i].color_weight:
            fmap[i].color_weight = progress
            if fmap[i].blend_color == (0, 0, 0):
                fmap[i].blend_color = (0, 0, 0)
    for k in range(min(fade_frames, n)):
        i = n - 1 - k
        progress = 1.0 - (k + 1) / fade_frames
        if (1.0 - progress) > fmap[i].color_weight:
            fmap[i].color_weight = 1.0 - progress
