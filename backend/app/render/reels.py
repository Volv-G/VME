"""Player reels: one video per player, all of that player's plays in a row.

A reel is a *separate* render kind, not a replacement for `highlights`.
Highlights stay one-file-per-play (good for local review and for dropping
a single clip into a message); reels exist because uploading 48 files per
match to YouTube is impractical - both against the per-day
`videos.insert` bucket and for whoever has to watch them. One reel per
player per match turns 48 uploads into ~12, each with a chapter list so a
viewer can still jump to an individual play.

Span construction reuses the highlight rally bounds (see
`batch.highlights_from_match`) and then applies three reel-specific
passes:

1. **Merge** overlapping / adjacent spans. Two player events in the same
   rally (Dig then Kill) produce two identical rally windows; a reel must
   show that rally once, labelled with both actions.
2. **Extend short spans** toward `MIN_CHAPTER_SECONDS`. An ace rally can
   be 4-5 s, and YouTube ignores a chapter list entirely if ANY chapter
   is under 10 s. Extending (rather than dropping) keeps the play and
   adds lead-in context; it never trims.
3. **Decide whether chapters are legal at all.** YouTube requires >= 3
   chapters, the first at 00:00, and every chapter >= 10 s. A player with
   two plays in a match can't have chapters - so the description falls
   back to a plain timestamp list, which YouTube still auto-links into
   seekable links even when the chapter UI doesn't appear.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Optional

from ..domain.events.player import PlayerEvent
from ..domain.match import Match
from ..domain.roster import NamingConfig, Roster
from ..domain.team import Team
from ..domain.tournament import Tournament
from . import naming
from .batch import (
    FALLBACK_HALF_WINDOW_SECONDS,
    MAX_RALLY_LEAD_SECONDS,
    MAX_RALLY_TAIL_SECONDS,
    RALLY_PAD_AFTER_SECONDS,
    RALLY_PAD_BEFORE_SECONDS,
    _fps,
    _indexed_events,
    _rally_bounds,
)

logger = logging.getLogger(__name__)


# YouTube's chapter rules (all must hold or the chapter bar doesn't
# appear): at least this many chapters, the first one starting exactly at
# 00:00, and no chapter shorter than MIN_CHAPTER_SECONDS.
MIN_CHAPTERS = 3
MIN_CHAPTER_SECONDS = 10.0
# What short segments are actually extended to. Deliberately above the
# 10 s rule: at 59.94 fps a "10 s" span rounds to 599 frames = 9.993 s,
# which would fail the very check we're extending to satisfy. The extra
# second also absorbs the frame or two a cut region can shave off a span.
CHAPTER_TARGET_SECONDS = 11.0
# Float slack when validating durations, so a segment that is 10.0000s
# minus one ULP isn't reported as too short.
_DURATION_EPSILON = 1e-3

# Spans closer together than this are merged into one segment rather than
# being cut apart and put back together - a sub-second gap of match
# footage reads as a glitch, and two chapters 0.4 s apart are useless.
MERGE_GAP_SECONDS = 1.5


@dataclass
class ReelSegment:
    """One play inside a reel.

    `start_frame`/`end_frame` are source/global frames (end exclusive),
    matching `MatchRenderer.render(source_frame_ranges=...)`.
    `actions` lists every player event that landed in this span, in
    chronological order, deduplicated (e.g. `["dig", "kill"]`).
    """

    start_frame: int
    end_frame: int
    actions: list[str] = field(default_factory=list)
    match_timestamp: str = ""

    @property
    def frames(self) -> int:
        return max(0, self.end_frame - self.start_frame)

    def label(self) -> str:
        """Human label for the chapter line: `Dig + Kill`."""
        if not self.actions:
            return "Highlight"
        return " + ".join(a.replace("_", " ").title() for a in self.actions)


@dataclass
class ReelSpec:
    """Everything needed to render + upload one player's reel."""

    jersey: int
    team: Team
    player_label: str  # "#8 Kate G" - for progress messages / titles
    segments: list[ReelSegment]
    relative_path: str
    label: str

    @property
    def total_frames(self) -> int:
        return sum(s.frames for s in self.segments)

    def source_ranges(self) -> list[tuple[int, int]]:
        return [(s.start_frame, s.end_frame) for s in self.segments]


# ---------------------------------------------------------------------------
# Span building
# ---------------------------------------------------------------------------


def _merge_segments(
    segments: list[ReelSegment], merge_gap_frames: int
) -> list[ReelSegment]:
    """Coalesce overlapping / near-adjacent spans, unioning their actions.

    Input must be sorted by `start_frame`. Two player events in one rally
    yield the same rally window twice; without this the reel would replay
    the rally per event and the chapter list would be full of duplicates.
    """
    merged: list[ReelSegment] = []
    for seg in segments:
        if merged and seg.start_frame - merged[-1].end_frame <= merge_gap_frames:
            prev = merged[-1]
            prev.end_frame = max(prev.end_frame, seg.end_frame)
            for action in seg.actions:
                if action not in prev.actions:
                    prev.actions.append(action)
            continue
        merged.append(seg)
    return merged


def _extend_to_minimum(
    segments: list[ReelSegment], target_frames: int, total_frames: int
) -> list[ReelSegment]:
    """Grow any segment shorter than `target_frames`, centered on itself.

    Needed because YouTube drops the whole chapter list if a single
    chapter is under 10 s, and a clean ace (serve -> point) can be 4 s.
    Growth is clamped to the match bounds, and re-merged afterwards in
    case the extension made two segments touch.
    """
    for seg in segments:
        deficit = target_frames - seg.frames
        if deficit <= 0:
            continue
        before = deficit // 2
        after = deficit - before
        new_start = max(0, seg.start_frame - before)
        # Push whatever we couldn't take from the front onto the back
        # (and vice versa) so a play near frame 0 still reaches the
        # minimum length.
        shortfall = before - (seg.start_frame - new_start)
        new_end = min(total_frames, seg.end_frame + after + shortfall)
        shortfall = after + shortfall - (new_end - seg.end_frame)
        if shortfall > 0:
            new_start = max(0, new_start - shortfall)
        seg.start_frame, seg.end_frame = new_start, new_end
    return segments


def reels_from_match(
    match: Match,
    home_roster: Roster,
    home_team_name: str,
    *,
    team: str = "",
    tournament: str = "",
    date: str = "",
    match_name: str = "",
    tournament_info: Optional[Tournament] = None,
    naming_config: Optional[NamingConfig] = None,
    only_home: bool = True,
) -> list[ReelSpec]:
    """One `ReelSpec` per player with at least one player-tagged event.

    `only_home` keeps opponent players out by default: their roster is
    usually unnamed and a reel of the other team isn't something anyone
    asks for. Order of reels follows jersey number; order of segments
    inside a reel is chronological.
    """
    fps = _fps(match)
    pad_before = int(round(RALLY_PAD_BEFORE_SECONDS * fps))
    pad_after = int(round(RALLY_PAD_AFTER_SECONDS * fps))
    fallback = int(round(FALLBACK_HALF_WINDOW_SECONDS * fps))
    max_lead = int(round(MAX_RALLY_LEAD_SECONDS * fps))
    max_tail = int(round(MAX_RALLY_TAIL_SECONDS * fps))
    merge_gap = int(round(MERGE_GAP_SECONDS * fps))
    # ceil, not round: rounding down would land under the minimum.
    target_frames = int(math.ceil(CHAPTER_TARGET_SECONDS * fps))
    total = match.total_frames()

    indexed = list(_indexed_events(match))
    nc = naming_config or NamingConfig()
    ti = tournament_info or Tournament()

    # Group raw spans per (team, jersey).
    grouped: dict[tuple[Team, int], list[ReelSegment]] = {}
    for ev, g in indexed:
        if not isinstance(ev, PlayerEvent):
            continue
        if only_home and ev.team != Team.HOME:
            continue
        start, end = _rally_bounds(
            indexed, g, fallback, max_lead=max_lead, max_tail=max_tail
        )
        start = max(0, start - pad_before)
        end = min(total, end + pad_after)
        if end <= start:
            continue
        grouped.setdefault((ev.team, ev.player_number), []).append(
            ReelSegment(
                start_frame=start,
                end_frame=end,
                actions=[ev.type_name],
                match_timestamp=naming.format_match_timestamp(g, fps),
            )
        )

    out: list[ReelSpec] = []
    for (team_enum, jersey), segments in sorted(
        grouped.items(), key=lambda kv: (kv[0][0].value, kv[0][1])
    ):
        segments.sort(key=lambda s: s.start_frame)
        segments = _merge_segments(segments, merge_gap)
        segments = _extend_to_minimum(segments, target_frames, total)
        # Extending can make neighbours touch - merge once more so the
        # reel never replays the same footage twice in a row.
        segments = _merge_segments(segments, merge_gap)

        vars_ = naming.build_reel_vars(
            team=team,
            tournament=tournament,
            date=date,
            match=match_name,
            match_obj=match,
            home_team_name=home_team_name,
            tournament_abbreviation=ti.abbreviation or "",
            tournament_full_name=ti.full_name or "",
            team_enum=team_enum,
            jersey=jersey,
            home_roster=home_roster,
            clip_count=len(segments),
        )
        try:
            rel = naming.render_naming_template(nc.reel_template, vars_)
        except naming.TemplateError as exc:
            logger.warning(
                "reel template failed for #%s: %s; falling back to default",
                jersey,
                exc,
            )
            rel = naming.render_naming_template(
                naming.DEFAULT_REEL_TEMPLATE, vars_
            )

        player_name = (vars_.get("player_name") or "").replace("_", " ")
        player_label = f"#{jersey} {player_name}".strip()
        out.append(
            ReelSpec(
                jersey=jersey,
                team=team_enum,
                player_label=player_label,
                segments=segments,
                relative_path=rel,
                label=f"{player_label} reel ({len(segments)} plays)",
            )
        )
    return out


# ---------------------------------------------------------------------------
# Chapters / description
# ---------------------------------------------------------------------------


def _timecode(seconds: float) -> str:
    """`mm:ss` (or `h:mm:ss`) - the format YouTube parses as a chapter.

    Minutes are zero-padded so the first line reads exactly `00:00`,
    which is what YouTube's chapter documentation requires.
    """
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


@dataclass
class ChapterList:
    """Chapter lines plus whether YouTube will actually honor them."""

    lines: list[str]
    valid: bool
    reason: str = ""

    def as_text(self) -> str:
        return "\n".join(self.lines)


def build_chapters(
    spans: list[tuple[int, int, int]],
    segments: list[ReelSegment],
    fps: float,
) -> ChapterList:
    """Chapter lines for a rendered reel.

    `spans` are the `(out_start, out_end, segment_index)` triples returned
    by `MatchRenderer.segment_offsets()`, so timecodes reflect what
    actually made it into the file. The segment index matters: a span
    that fell entirely inside a cut region is missing from the list, and
    pairing positionally would then label every later chapter with the
    wrong play.

    Validity is reported rather than enforced: the caller still writes
    the timestamp list (YouTube auto-links bare timestamps in a
    description even when the chapter bar is suppressed), it just knows
    not to promise chapters.
    """
    lines: list[str] = []
    durations: list[float] = []
    for out_start, out_end, seg_index in spans:
        seg = segments[seg_index]
        lines.append(
            f"{_timecode(out_start / fps)} {seg.label()}"
            + (f" ({seg.match_timestamp})" if seg.match_timestamp else "")
        )
        durations.append((out_end - out_start) / fps)

    if not lines:
        return ChapterList(lines=[], valid=False, reason="no segments")
    if len(lines) < MIN_CHAPTERS:
        return ChapterList(
            lines=lines,
            valid=False,
            reason=(
                f"only {len(lines)} chapter(s); YouTube needs at least "
                f"{MIN_CHAPTERS}"
            ),
        )
    if spans[0][0] != 0:
        return ChapterList(
            lines=lines,
            valid=False,
            reason="first chapter does not start at 00:00",
        )
    short = [d for d in durations if d < MIN_CHAPTER_SECONDS - _DURATION_EPSILON]
    if short:
        return ChapterList(
            lines=lines,
            valid=False,
            reason=(
                f"{len(short)} chapter(s) shorter than {MIN_CHAPTER_SECONDS:g}s "
                f"(shortest {min(short):.1f}s)"
            ),
        )
    return ChapterList(lines=lines, valid=True)
