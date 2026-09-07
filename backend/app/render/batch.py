"""Per-event clip extraction for highlights / focused-highlights jobs.

This module turns a `Match` into a list of `BatchClip` specs, each of
which the renderer can produce as a standalone mp4 via the existing
`source_frame_range` plumbing.

Two kinds of spans are produced:

- `highlights_from_match`: one clip per `HighlightEvent`, bounded by the
  rally containing it. Rally start = nearest preceding `BallServedEvent`;
  rally end = nearest following rally-ending event (next BallServed or a
  scoring event). A small pad is added on each side for context.

- `focused_from_match`: one clip per matching `FocusInEvent` /
  `FocusOutEvent` pair. The user has explicitly marked the bounds, so we
  honor them exactly with no extra pad.

`BatchClip` is intentionally lightweight (start/end global frames, the
relative output path, and a short label for progress messages). The
actual rendering happens in `render_job.py`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterator, Optional

from ..domain.events.focus import FocusInEvent, FocusOutEvent
from ..domain.events.lifecycle import GameEndEvent, GameStartEvent, SetEndEvent
from ..domain.events.player import (
    AceEvent,
    KillEvent,
    PlayerEvent,
)
from ..domain.events.scoring import ScoreEvent
from ..domain.events.serve import BallServedEvent
from ..domain.events.timeline import CutStartEvent
from ..domain.match import Match
from ..domain.roster import NamingConfig, Roster
from ..domain.tournament import Tournament
from . import naming

logger = logging.getLogger(__name__)


# Context padding around a rally's serve/score bounds, in seconds. Keeps
# a small amount of pre-serve setup and post-point celebration so the
# highlight clip doesn't feel surgically cropped.
RALLY_PAD_BEFORE_SECONDS = 1.0
RALLY_PAD_AFTER_SECONDS = 2.0

# Fallback half-window when no serve / scoring event surrounds a
# highlight. Real matches should always have BallServed events near
# highlights, but legacy / lightly-annotated matches won't - we still
# want to produce *something* watchable in that case.
FALLBACK_HALF_WINDOW_SECONDS = 5.0

# Hard caps on how far a rally window may reach from the tagged event,
# applied AFTER the boundary search. A volleyball rally is seconds long;
# anything longer means the annotation is missing a boundary (no
# `ball_served` logged for the next rally, a set break, a timeout...) and
# without a cap one Dig can pull in 90+ seconds of bench footage.
# Asymmetric on purpose: a tagged action sits near the END of its rally,
# so more room is needed before it than after.
MAX_RALLY_LEAD_SECONDS = 20.0
MAX_RALLY_TAIL_SECONDS = 12.0

# Events that end a rally. `score` matters most: a rally the OPPONENT
# wins produces no Kill/Ace of ours, so without it the window ran on
# until the next logged serve - which can be a set break away.
_RALLY_END_TYPES = (
    KillEvent,
    AceEvent,
    ScoreEvent,
    SetEndEvent,
    GameEndEvent,
)

# Action label used for a focus span where the player has no tagged
# event in the span or the surrounding rally. The span still renders -
# the user bracketed that moment on purpose - it just lands in the
# generic `highlight` bucket of the naming template.
GENERIC_FOCUS_ACTION = "highlight"


@dataclass(frozen=True)
class BatchClip:
    """One clip in a batch render.

    Attributes:
        start_frame, end_frame: source/global frame range. `end_frame`
            is exclusive (consistent with `MatchRenderer.source_frame_range`).
        relative_path: output path *relative to the match's renders dir*.
            E.g. `highlights/Home_Team/07_Mia/f110190.mp4`.
        label: short string for progress messages (e.g.
            `"#7 Mia @ frame 110190"`).
    """

    start_frame: int
    end_frame: int
    relative_path: str
    label: str


# ---------------------------------------------------------------------------
# Highlights
# ---------------------------------------------------------------------------


def highlights_from_match(
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
) -> list[BatchClip]:
    """Build a `BatchClip` for every player-attributed event in the match.

    Includes ALL `PlayerEvent` subclasses (Kill, Ace, Assist, Block, Dig,
    Dive, Highlight) - the user thinks of every player-tagged moment as
    "a highlight for that player". Each becomes one clip of the rally
    containing it, organized by team / player number / player name.

    Multiple player events can fall inside the same rally (e.g. Dig +
    Assist + Kill); each gets its own output file so a player whose dig
    set up a teammate's kill still has a clip of that rally in their
    folder.
    """
    fps = _fps(match)
    pad_before = int(round(RALLY_PAD_BEFORE_SECONDS * fps))
    pad_after = int(round(RALLY_PAD_AFTER_SECONDS * fps))
    fallback = int(round(FALLBACK_HALF_WINDOW_SECONDS * fps))
    max_lead = int(round(MAX_RALLY_LEAD_SECONDS * fps))
    max_tail = int(round(MAX_RALLY_TAIL_SECONDS * fps))
    total = match.total_frames()

    # Pre-compute global frames of all events once; saves O(N^2) scans
    # later when finding rally bounds for each highlight.
    indexed = list(_indexed_events(match))

    nc = naming_config or NamingConfig()
    ti = tournament_info or Tournament()

    out: list[BatchClip] = []
    for ev, g in indexed:
        if not isinstance(ev, PlayerEvent):
            continue
        start, end = _rally_bounds(
            indexed, g, fallback, max_lead=max_lead, max_tail=max_tail
        )
        start = max(0, start - pad_before)
        end = min(total, end + pad_after)
        if end <= start:
            continue

        ts = naming.format_match_timestamp(g, fps)
        vars_ = naming.build_highlight_vars(
            team=team,
            tournament=tournament,
            date=date,
            match=match_name,
            match_obj=match,
            home_team_name=home_team_name,
            tournament_abbreviation=ti.abbreviation or "",
            tournament_full_name=ti.full_name or "",
            team_enum=ev.team,
            jersey=ev.player_number,
            home_roster=home_roster,
            action=ev.type_name,
            match_timestamp=ts,
        )
        try:
            rel = naming.render_naming_template(nc.highlight_template, vars_)
        except naming.TemplateError as exc:
            logger.warning(
                "highlight template failed for #%s %s @ %s: %s",
                ev.player_number,
                ev.type_name,
                ts,
                exc,
            )
            continue
        label = (
            f"#{ev.player_number} {vars_.get('team', '?')} "
            f"{ev.type_name} @ {ts}"
        )
        out.append(
            BatchClip(
                start_frame=start,
                end_frame=end,
                relative_path=rel,
                label=label,
            )
        )
    return out


def _rally_bounds(
    indexed: list[tuple],
    highlight_global: int,
    fallback_half: int,
    *,
    max_lead: Optional[int] = None,
    max_tail: Optional[int] = None,
) -> tuple[int, int]:
    """Return (start_global, end_global) of the rally containing a frame.

    `indexed` is a list of `(event, global_frame)` tuples sorted by
    global_frame (events without a resolvable global frame already filtered
    out).

    Bounds:
      - rally start: nearest preceding `BallServedEvent` (g <= highlight),
        but never earlier than a `cut_start` between it and the highlight -
        footage the user marked for removal shouldn't define a rally.
      - rally end, whichever comes first at g >= highlight:
          * a rally-ending event (`_RALLY_END_TYPES`): our Kill/Ace, a
            `score` for either team, or a set/game end. `>=` rather than
            `>` so when the highlight IS the kill, the rally ends *with*
            it instead of running into the next rally;
          * the next `BallServedEvent` at g > highlight;
          * a `cut_start` at g > highlight.

    Both sides are then clamped to `max_lead` / `max_tail` frames around
    the highlight when given. That clamp is the safety net for missing
    annotation: a Dig whose rally was won by the opponent used to extend
    to the next logged serve, which after a set break was 90 seconds of
    bench footage.

    When no boundary exists on a side, we fall back to a symmetric
    half-window so the user still gets a watchable clip.
    """
    start: Optional[int] = None
    end: Optional[int] = None
    # One chronological pass: the last opening boundary at or before the
    # highlight wins, and the FIRST closing boundary at or after it wins
    # (whichever kind it is - rally-ender, next serve, or a cut).
    for ev, g in indexed:
        if g <= highlight_global and isinstance(
            ev, (BallServedEvent, CutStartEvent, GameStartEvent)
        ):
            start = g
            continue
        if g >= highlight_global:
            if isinstance(ev, _RALLY_END_TYPES):
                end = g
                break
            if g > highlight_global and isinstance(
                ev, (BallServedEvent, CutStartEvent)
            ):
                end = g
                break

    if start is None:
        start = highlight_global - fallback_half
    if end is None:
        end = highlight_global + fallback_half

    if max_lead is not None:
        start = max(start, highlight_global - max_lead)
    if max_tail is not None:
        end = min(end, highlight_global + max_tail)
    return start, end


# ---------------------------------------------------------------------------
# Focused highlights
# ---------------------------------------------------------------------------


def focused_from_match(
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
) -> list[BatchClip]:
    """Build a `BatchClip` for every matched FocusIn/FocusOut pair.

    Pairing is stack-based: each FocusIn opens a new segment, the next
    FocusOut closes the most recent open one. Unpaired events at the end
    are skipped with a warning - they represent incomplete annotation
    and rendering them with an arbitrary endpoint would be misleading.
    """
    indexed = list(_indexed_events(match))
    open_stack: list[tuple[FocusInEvent, int]] = []
    spans: list[tuple[FocusInEvent, int, int]] = []  # (in_event, start, end)

    for ev, g in indexed:
        if isinstance(ev, FocusInEvent):
            open_stack.append((ev, g))
        elif isinstance(ev, FocusOutEvent):
            if not open_stack:
                logger.warning(
                    "FocusOut at frame %d with no matching FocusIn; skipping",
                    g,
                )
                continue
            in_ev, in_g = open_stack.pop()
            if g <= in_g:
                logger.warning(
                    "FocusOut at %d not after FocusIn at %d; skipping",
                    g,
                    in_g,
                )
                continue
            spans.append((in_ev, in_g, g))

    if open_stack:
        logger.warning(
            "%d FocusIn event(s) without a matching FocusOut; skipping",
            len(open_stack),
        )

    fps = _fps(match)
    fallback = int(round(FALLBACK_HALF_WINDOW_SECONDS * fps))
    nc = naming_config or NamingConfig()
    ti = tournament_info or Tournament()

    out: list[BatchClip] = []
    for in_ev, start, end in spans:
        start_ts = naming.format_match_timestamp(start, fps)
        end_ts = naming.format_match_timestamp(end, fps)
        # Action label for the focused clip: lift the FIRST PlayerEvent
        # for the same player, looking inside the marked span first and
        # only then at the surrounding rally. A focus span with no player
        # event anywhere is still rendered, as a generic highlight - the
        # user deliberately bracketed that moment, so dropping it loses
        # work.
        action = _first_action_for_player(
            indexed, start, end, in_ev.team, in_ev.player_number, fallback
        )
        if action is None:
            action = GENERIC_FOCUS_ACTION
            logger.info(
                "focused clip for #%d at %s has no tagged player event; "
                "labelling it '%s'",
                in_ev.player_number,
                start_ts,
                action,
            )
        vars_ = naming.build_focused_vars(
            team=team,
            tournament=tournament,
            date=date,
            match=match_name,
            match_obj=match,
            home_team_name=home_team_name,
            tournament_abbreviation=ti.abbreviation or "",
            tournament_full_name=ti.full_name or "",
            team_enum=in_ev.team,
            jersey=in_ev.player_number,
            home_roster=home_roster,
            start_timestamp=start_ts,
            end_timestamp=end_ts,
            action=action,
        )
        try:
            rel = naming.render_naming_template(nc.focused_template, vars_)
        except naming.TemplateError as exc:
            logger.warning(
                "focused template failed for #%s @ %s-%s: %s",
                in_ev.player_number,
                start_ts,
                end_ts,
                exc,
            )
            continue
        label = (
            f"#{in_ev.player_number} {vars_.get('team', '?')} "
            f"{start_ts}-{end_ts}"
        )
        out.append(
            BatchClip(
                start_frame=start,
                end_frame=end,
                relative_path=rel,
                label=label,
            )
        )
    return out


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _indexed_events(match: Match) -> Iterator[tuple]:
    """Yield `(event, global_frame)` for every event with a resolvable
    global frame. Events sorted by `match._sort_events()` already, so the
    output is in chronological order."""
    for ev in match.events:
        g = match.to_global_frame(ev.clip_id, ev.local_frame)
        if g is None:
            continue
        yield ev, g


def _first_action_for_player(
    indexed: list[tuple],
    focus_in_global: int,
    focus_out_global: int,
    player_team,  # Team enum; typed loosely to avoid forward ref churn
    player_number: int,
    fallback_half: int,
) -> Optional[str]:
    """Action label for a focus span, or None if the player did nothing
    tagged nearby.

    Two passes, in priority order:

    1. Inside the marked span `[focus_in, focus_out]`. The user drew
       those bounds around the play, so anything they tagged in there is
       by definition what the clip is about. This pass is what makes a
       FocusIn placed *before* the serve work: the rally heuristic below
       would otherwise attribute such a focus to the PREVIOUS rally
       (the next `ball_served` closes the window before the play even
       starts) and miss the Ace/Kill that follows.
    2. The rally containing the FocusIn, using the same `_rally_bounds`
       heuristic highlights use, so a focused clip's action label matches
       whatever the highlight clip for that rally would carry. Covers a
       tight focus span that ends before the player's action was logged.
    """

    def _scan(lo: int, hi: int) -> Optional[str]:
        for ev, g in indexed:
            if g < lo:
                continue
            if g > hi:
                break
            if (
                isinstance(ev, PlayerEvent)
                and ev.team == player_team
                and ev.player_number == player_number
            ):
                return ev.type_name
        return None

    found = _scan(focus_in_global, focus_out_global)
    if found is not None:
        return found

    rally_start, rally_end = _rally_bounds(
        indexed, focus_in_global, fallback_half
    )  # no clamp: this window is only used to LOOK UP an action label,
    # never to cut footage, so a loose rally guess is harmless here.
    return _scan(rally_start, rally_end)


def _fps(match: Match) -> float:
    """Project FPS for pad arithmetic. Mirrors the renderer's choice of
    `clips[0].fps` first, `match.fps` second."""
    if match.clips and match.clips[0].fps > 0:
        return match.clips[0].fps
    return match.fps or 30.0
