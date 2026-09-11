"""Condensed match render: the plays, nothing else.

A full match render is an hour of footage of which roughly a third is
actual volleyball - the rest is players walking back to position, balls
being retrieved, timeouts, and the scorer's table. This builder keeps
only the rallies: from just before each serve to just after the point
it produced, cut straight to the next one.

Measured on two real matches (Eastlake_JV vs Liberty 2026-09-03 and vs
Mercer Island 2026-09-09, 134 rallies each):

    match          full      play time   ratio
    Liberty        66.4 min  22.7 min     34%
    Mercer Island  72.7 min  35.0 min     48%

What ends a rally
-----------------
The first of `kill` / `ace` / `score` after the serve. In this data set
the score is only logged explicitly when the OPPONENT wins the point -
our own points come as a Kill or an Ace, which carry the score update
themselves. Both matches: 105-114 rallies ended on `score`, 11-17 on
`kill`, 9-12 on `ace`, and exactly 3 serves had no outcome logged at
all (mis-tagged serves), which fall back to a fixed window.

Popups from the dead time
-------------------------
Substitutions are logged while play is stopped - which is footage this
render removes - so their popups would vanish. They are instead deferred
to the start of the next play and staggered, so a set break that swaps
six players plays them as a cascade over the first seconds of the next
rally instead of stacking six boxes on one frame.

Set breaks
----------
Rallies are joined by hard cuts (that's the point: no dead air), except
where a `set_end` falls in the removed gap. There the outgoing rally
fades to black and the next set fades up, so the render reads as three
sets rather than one continuous stream of points.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from ..domain.effects import MessagePopupEffect, PlayerPopupEffect, TimelineEffect
from ..domain.events.base import MatchEvent
from ..domain.match import Match
from .frame_map import FrameEntry, FrameMap
from .frame_map_builder import (
    _apply_effect_at_join,
    _collect_cut_regions,
    apply_bookend_fades,
    apply_overlay_state,
    apply_timeline_effects,
    index_events,
    pass1_concat,
)

logger = logging.getLogger(__name__)

# Event type names that open a play. `first_serve` is the first serve of
# a set; it opens a rally exactly like any other serve.
SERVE_TYPES = frozenset({"ball_served", "first_serve"})

# Event type names that close a play, i.e. "the point is decided". NOT
# `set_end` / `game_end`: those are bookkeeping logged after the last
# point's own score event, so treating them as enders would clip the
# final rally of every set short.
POINT_TYPES = frozenset({"kill", "ace", "score", "score_correction"})

# Event type names that mark a set boundary - the only joins that get a
# fade instead of a hard cut.
SET_BREAK_TYPES = frozenset({"set_end"})

# Context kept around each play, in seconds. Before: the toss, since the
# serve is tagged at contact. After: seeing the ball land and the
# immediate reaction. Matches the highlight renderer's padding (see
# `batch.RALLY_PAD_*`) so a rally looks the same in both products.
#
# This is not free: at ~134 rallies a match, every second of padding adds
# 2.2 minutes to the output. 1.5 + 2.0 costs ~7.8 min on top of the 23
# min of actual play in the reference match - which is the difference
# between "condensed" and "surgical".
PAD_BEFORE_SECONDS = 1.5
PAD_AFTER_SECONDS = 2.0

# Two plays whose padded windows end up this close are merged rather
# than cut apart - a sub-second sliver of removed footage between two
# rallies is a visible jump cut that saves nothing.
MERGE_GAP_SECONDS = 1.5

# Window length for a serve with no outcome logged (3 per match in the
# reference data). Long enough to contain a short rally, short enough
# that a mis-tagged serve doesn't drag in a timeout.
FALLBACK_PLAY_SECONDS = 12.0

# Ceiling on one play. The reference matches have p90 = 18-27s and a
# single 45s outlier caused by a late-logged score, so this only trims
# pathological windows; real rallies are never cut short by it.
MAX_PLAY_SECONDS = 45.0

# Fade length at a set break (each side of the join).
SET_FADE_SECONDS = 0.7

# Gap between deferred popups that land on the same play, so six
# substitutions cascade instead of stacking. At the default 3s popup
# duration that means at most two on screen at once.
DEFERRED_POPUP_STRIDE_SECONDS = 1.5


@dataclass
class CondensedStats:
    """What the condense pass decided, for logging and job messages."""

    plays: int
    set_breaks: int
    deferred_popups: int
    source_frames: int
    output_frames: int

    @property
    def ratio(self) -> float:
        return self.output_frames / self.source_frames if self.source_frames else 0.0

    def summary(self, fps: float) -> str:
        if not fps:
            fps = 30.0
        return (
            f"{self.plays} plays, {self.output_frames / fps / 60:.1f} min of "
            f"{self.source_frames / fps / 60:.1f} min ({self.ratio * 100:.0f}%), "
            f"{self.set_breaks} set break(s), "
            f"{self.deferred_popups} popup(s) moved into the next play"
        )


@dataclass
class _Window:
    """A play, in output-frame coordinates of the full frame map."""

    start: int  # inclusive
    end: int  # exclusive
    fade_in: bool = False  # a set break precedes this window


def build_condensed_frame_map(
    match: Match,
    *,
    bookend_fade_seconds: float = 1.0,
    popup_duration_seconds: float = 3.0,
) -> tuple[FrameMap, CondensedStats]:
    """Build a FrameMap containing only the plays.

    Mirrors `build_frame_map` (same passes, same effects) with a
    keep-only-the-rallies step inserted between the timeline pass and
    the overlay pass - the order matters:

    * after the timeline pass, because cut regions and clip transitions
      have already re-seated frames and every index we compute must
      refer to the final footage;
    * before the overlay pass, so popups are placed on the frames that
      actually survive rather than being computed and then thrown away
      with the dead time they fired in.
    """
    fps = match.fps or 30.0
    full = FrameMap(fps=fps)

    cut_regions = _collect_cut_regions(match)
    pass1_concat(match, full, cut_regions)
    apply_timeline_effects(match, full, cut_regions)

    source_frames = len(full)
    if source_frames == 0:
        raise ValueError("Frame map is empty (no frames to render)")

    indexed = index_events(match, full)
    windows = _play_windows(indexed, total=source_frames, fps=fps)
    if not windows:
        raise ValueError(
            "No plays found: a condensed render needs serve events "
            "(`ball_served`) in the match. Log serves, or render the full "
            "match instead."
        )

    mark_set_breaks(indexed, windows)

    condensed, old_to_new = _slice(full, windows)
    remapped, deferred = _remap_events(indexed, windows, old_to_new, fps)

    apply_overlay_state(
        match,
        condensed,
        popup_duration_seconds=popup_duration_seconds,
        indexed=remapped,
    )

    set_breaks = _apply_set_fades(condensed, windows, old_to_new, fps)
    apply_bookend_fades(condensed, fade_frames=int(bookend_fade_seconds * fps))

    stats = CondensedStats(
        plays=len(windows),
        set_breaks=set_breaks,
        deferred_popups=deferred,
        source_frames=source_frames,
        output_frames=len(condensed),
    )
    logger.info("condensed render: %s", stats.summary(fps))
    return condensed, stats


# ----- Window detection ------------------------------------------------------


def _play_windows(
    indexed: list[tuple[int, MatchEvent]], *, total: int, fps: float
) -> list[_Window]:
    """Find the padded, merged play windows in output coordinates."""
    pad_before = int(round(PAD_BEFORE_SECONDS * fps))
    pad_after = int(round(PAD_AFTER_SECONDS * fps))
    fallback = int(round(FALLBACK_PLAY_SECONDS * fps))
    max_play = int(round(MAX_PLAY_SECONDS * fps))

    raw: list[tuple[int, int]] = []
    for position, (idx, ev) in enumerate(indexed):
        if ev.type_name not in SERVE_TYPES:
            continue
        end: Optional[int] = None
        for later_idx, later_ev in indexed[position + 1 :]:
            if later_ev.type_name in POINT_TYPES:
                end = later_idx
                break
            # Another serve before any point means this rally's outcome
            # was never logged - stop looking rather than swallowing the
            # next rally into this window.
            if later_ev.type_name in SERVE_TYPES:
                break
        if end is None:
            end = idx + fallback
        end = min(end, idx + max_play)
        raw.append((max(0, idx - pad_before), min(total, end + pad_after)))

    if not raw:
        return []

    # Merge overlaps and near-touching windows. `raw` is already in
    # ascending order (indexed is sorted), but padding can make
    # neighbours overlap.
    merge_gap = int(round(MERGE_GAP_SECONDS * fps))
    merged: list[list[int]] = [list(raw[0])]
    for start, end in raw[1:]:
        if start - merged[-1][1] <= merge_gap:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])

    return [_Window(start=s, end=e) for s, e in merged if e > s]


# ----- Slicing ---------------------------------------------------------------


def _slice(
    full: FrameMap, windows: list[_Window]
) -> tuple[FrameMap, dict[int, int]]:
    """Copy the kept frames into a new map; return it plus an index map.

    The entries themselves are re-used (not copied): the full map is
    discarded immediately afterwards, and a full-match map is ~240,000
    dataclass instances that there's no reason to duplicate.
    """
    condensed = FrameMap(fps=full.fps)
    old_to_new: dict[int, int] = {}
    for window in windows:
        for i in range(window.start, min(window.end, len(full))):
            old_to_new[i] = len(condensed)
            condensed.append(full[i])
    if len(condensed) == 0:
        # Defensive: windows were computed from this map's length, so
        # this would mean an empty window list slipped through.
        condensed.append(FrameEntry())
    return condensed, old_to_new


def _remap_events(
    indexed: list[tuple[int, MatchEvent]],
    windows: list[_Window],
    old_to_new: dict[int, int],
    fps: float,
) -> tuple[list[tuple[int, MatchEvent]], int]:
    """Place every event on a frame that exists in the condensed map.

    Events inside a kept window keep their moment. Events from removed
    footage move to the start of the NEXT play - that's where a
    substitution logged during a break belongs, since the next rally is
    the first time the viewer sees the new lineup. Events after the last
    play have no next play and are dropped.

    Popups that would land on the same frame are then staggered so they
    cascade. That applies to co-located popups generally, not just
    deferred ones: a set break that swaps six players logs six
    substitutions at one frame, and six boxes stacked up the side of the
    screen is unreadable whether or not this render moved them.
    """
    stride = max(1, int(round(DEFERRED_POPUP_STRIDE_SECONDS * fps)))
    # (new_start, new_last) per window, for "next play" lookups and for
    # clamping a staggered popup inside the play it belongs to.
    spans: list[tuple[int, int, int]] = []  # (old_start, new_start, new_last)
    for w in windows:
        new_start = old_to_new.get(w.start)
        if new_start is None:
            continue
        last_old = min(w.end, max(old_to_new) + 1) - 1
        new_last = old_to_new.get(last_old, new_start)
        spans.append((w.start, new_start, new_last))

    remapped: list[tuple[int, MatchEvent]] = []
    occupied: dict[int, int] = {}
    deferred = 0
    for idx, ev in indexed:
        new_idx = old_to_new.get(idx)
        if new_idx is None:
            target = next(
                (new_start for old_start, new_start, _ in spans if old_start > idx),
                None,
            )
            if target is None:
                continue  # nothing left to attach it to
            new_idx = target
            if _has_popup(ev):
                deferred += 1
        if _has_popup(ev):
            rank = occupied.get(new_idx, 0)
            occupied[new_idx] = rank + 1
            if rank:
                limit = next(
                    (
                        new_last
                        for _, new_start, new_last in spans
                        if new_start <= new_idx <= new_last
                    ),
                    new_idx,
                )
                new_idx = min(new_idx + rank * stride, limit)
        remapped.append((new_idx, ev))

    remapped.sort(key=lambda p: p[0])
    return remapped, deferred


def _has_popup(ev: MatchEvent) -> bool:
    """Does this event draw a popup? Only those need staggering."""
    try:
        effect = ev.overlay_effect_for_state(ev.state)
    except Exception:  # noqa: BLE001 - a broken event must not stop a render
        return False
    return isinstance(effect, (PlayerPopupEffect, MessagePopupEffect))


# ----- Set breaks ------------------------------------------------------------


def _apply_set_fades(
    condensed: FrameMap,
    windows: list[_Window],
    old_to_new: dict[int, int],
    fps: float,
) -> int:
    """Fade across every join that spans a set break. Returns the count.

    Reuses the same join machinery as cut/transition fades, so a set
    break looks like every other fade in the product rather than a
    second implementation of the same idea.
    """
    fade_frames = int(round(SET_FADE_SECONDS * fps))
    if fade_frames <= 0:
        return 0
    effect = TimelineEffect(fade_frames=fade_frames, blend_color=(0, 0, 0))
    applied = 0
    for window in windows:
        if not window.fade_in:
            continue
        join = old_to_new.get(window.start)
        if join is None or join <= 0 or join >= len(condensed):
            continue
        _apply_effect_at_join(condensed, join, effect)
        applied += 1
    return applied


def mark_set_breaks(
    indexed: list[tuple[int, MatchEvent]], windows: list[_Window]
) -> None:
    """Flag windows that open a new set (a `set_end` sits in the gap).

    Separate from window detection so the rule stays readable: a break
    is about what happened in the footage we removed, not about the
    plays themselves.
    """
    breaks = [idx for idx, ev in indexed if ev.type_name in SET_BREAK_TYPES]
    if not breaks:
        return
    for previous, window in zip(windows, windows[1:]):
        if any(previous.end <= b < window.start for b in breaks):
            window.fade_in = True
