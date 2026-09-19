"""Player reels: one video per player, all of that player's plays in a row.

A reel is a *separate* render kind, not a replacement for `highlights`.
Highlights stay one-file-per-play (good for local review and for dropping
a single clip into a message); reels exist because uploading 48 files per
match to YouTube is impractical - both against the per-day
`videos.insert` bucket and for whoever has to watch them. One reel per
player per match turns 48 uploads into ~12, each with a timestamp index
so a viewer can still jump to an individual play.

Span construction reuses the highlight rally bounds (see
`batch.highlights_from_match`) and then merges overlapping / adjacent
spans: two player events in the same rally (Dig then Kill) produce two
identical rally windows, and a reel must show that rally once, labelled
with both actions.

No chapters. Reels used to be padded out to an 11 s floor because
YouTube discards an entire chapter list if ANY chapter is under 10 s,
and also demands at least 3 chapters starting at 00:00 - conditions a
real reel fails more often than it meets (a player with two plays can
never satisfy them). Paying for that with a second of dead footage on
every play of every reel, to *sometimes* get a chapter bar, was a bad
trade. The description still carries the timestamp list, which YouTube
auto-links into seekable links regardless of the chapter rules - which
is the part that actually got used.
"""

from __future__ import annotations

import logging
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


# Spans closer together than this are merged into one segment rather than
# being cut apart and put back together - a sub-second gap of match
# footage reads as a glitch, and two chapters 0.4 s apart are useless.
MERGE_GAP_SECONDS = 1.5

# Default distance from the TAGGED EVENT that a reel segment may reach,
# applied after the rally bounds and their padding. Tighter than the
# highlight caps (20 s lead / 12 s tail) on purpose: a highlight clip is
# one play watched deliberately, while a reel is 10-20 plays in a row,
# and the rally lead-in that gives a single clip context becomes 3
# minutes of serve-receive in a reel. The rally bounds still win when
# they are tighter - this only caps them, it never reaches past the
# serve or the point into neighbouring rallies.
#
# Strongly asymmetric, and for the same reason the rally caps are: an
# action is tagged when it FINISHES, so everything that makes it worth
# watching - the pass, the set, the approach - is behind it, while what
# follows is a ball on the floor. 10 s covers a whole rally's build-up;
# 2 s is the point landing.
#
# Overridable per match (`Match.reel_lead_seconds` / `reel_tail_seconds`)
# because tagging habits differ: someone who tags on contact needs less
# lead than someone who tags when the whistle goes.
REEL_MAX_LEAD_SECONDS = 10.0
REEL_MAX_TAIL_SECONDS = 2.0

# Two tagged events this close together are one moment, not two, and the
# reel shows them as a single continuous segment. Measured between the
# EVENTS, not between their windows: with the caps above, two events 5 s
# apart in the same rally already produce overlapping windows, but the
# same two events either side of a rally boundary get their windows
# clipped at the point and the next serve - leaving a gap wide enough to
# survive `MERGE_GAP_SECONDS` and split a single continuous bit of play
# into two chapters with a jump cut between them.
SAME_MOMENT_SECONDS = 5.0


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
    # Global frames of the tagged events this segment was built from, in
    # order. Kept because merging decisions are about how far apart the
    # ACTIONS are, which a start/end pair can no longer tell you once a
    # window has been padded, capped and extended.
    event_frames: list[int] = field(default_factory=list)

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
    segments: list[ReelSegment],
    merge_gap_frames: int,
    same_moment_frames: int = 0,
) -> list[ReelSegment]:
    """Coalesce overlapping / near-adjacent spans, unioning their actions.

    Input must be sorted by `start_frame`. Two player events in one rally
    yield the same rally window twice; without this the reel would replay
    the rally per event and the chapter list would be full of duplicates.

    Merging happens on either of two grounds:

    * the windows touch (within `merge_gap_frames`) - a sub-second sliver
      of footage between two clips reads as a glitch; or
    * the tagged events are within `same_moment_frames` of each other,
      whatever their windows ended up looking like. A dig and the kill it
      set up are one piece of play; showing them as two chapters with a
      cut in between misrepresents what happened.

    The merged segment covers everything from the first start to the last
    end, including any footage between the two windows - continuous play
    is the point.
    """
    merged: list[ReelSegment] = []
    for seg in segments:
        prev = merged[-1] if merged else None
        if prev is not None:
            close_windows = seg.start_frame - prev.end_frame <= merge_gap_frames
            close_events = (
                same_moment_frames > 0
                and prev.event_frames
                and seg.event_frames
                and seg.event_frames[0] - prev.event_frames[-1]
                <= same_moment_frames
            )
            if close_windows or close_events:
                prev.end_frame = max(prev.end_frame, seg.end_frame)
                prev.start_frame = min(prev.start_frame, seg.start_frame)
                prev.event_frames.extend(seg.event_frames)
                for action in seg.actions:
                    if action not in prev.actions:
                        prev.actions.append(action)
                continue
        merged.append(seg)
    return merged


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
    lead_seconds = (
        match.reel_lead_seconds
        if match.reel_lead_seconds is not None
        else REEL_MAX_LEAD_SECONDS
    )
    tail_seconds = (
        match.reel_tail_seconds
        if match.reel_tail_seconds is not None
        else REEL_MAX_TAIL_SECONDS
    )
    reel_lead = int(round(lead_seconds * fps))
    reel_tail = int(round(tail_seconds * fps))
    merge_gap = int(round(MERGE_GAP_SECONDS * fps))
    same_moment = int(round(SAME_MOMENT_SECONDS * fps))
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
        # Never more than a few seconds either side of the action the
        # segment exists for. Applied last so it caps the padding too:
        # the rally bounds decide where the play is, this decides how
        # much of it a reel is willing to spend. With the chapter floor
        # gone this is the only thing deciding segment length, so a
        # segment is now exactly as long as the play it shows.
        start = max(start, g - reel_lead)
        end = min(end, g + reel_tail + 1)
        if end <= start:
            continue
        grouped.setdefault((ev.team, ev.player_number), []).append(
            ReelSegment(
                start_frame=start,
                end_frame=end,
                actions=[ev.type_name],
                match_timestamp=naming.format_match_timestamp(g, fps),
                event_frames=[g],
            )
        )

    out: list[ReelSpec] = []
    for (team_enum, jersey), segments in sorted(
        grouped.items(), key=lambda kv: (kv[0][0].value, kv[0][1])
    ):
        segments.sort(key=lambda s: s.start_frame)
        segments = _merge_segments(segments, merge_gap, same_moment)

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
# Timestamp index for the description
# ---------------------------------------------------------------------------


def _timecode(seconds: float) -> str:
    """`mm:ss` (or `h:mm:ss`) - the format YouTube turns into a link.

    Minutes are zero-padded for alignment in the description; YouTube
    links `0:07` and `00:07` alike.
    """
    total = int(seconds)
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


@dataclass
class TimestampList:
    """The per-play timestamp lines that go in a reel's description."""

    lines: list[str]

    def as_text(self) -> str:
        return "\n".join(self.lines)


def build_timestamps(
    spans: list[tuple[int, int, int]],
    segments: list[ReelSegment],
    fps: float,
) -> TimestampList:
    """Timestamp lines for a rendered reel.

    `spans` are the `(out_start, out_end, segment_index)` triples returned
    by `MatchRenderer.segment_offsets()`, so timecodes reflect what
    actually made it into the file. The segment index matters: a span
    that fell entirely inside a cut region is missing from the list, and
    pairing positionally would then label every later line with the
    wrong play.

    Nothing is validated. These are links in a description, not chapters,
    so there is no minimum count, no 00:00 requirement and no minimum
    duration to satisfy - a two-play reel gets two working links.
    """
    lines: list[str] = []
    for out_start, _out_end, seg_index in spans:
        seg = segments[seg_index]
        lines.append(
            f"{_timecode(out_start / fps)} {seg.label()}"
            + (f" ({seg.match_timestamp})" if seg.match_timestamp else "")
        )
    return TimestampList(lines=lines)
