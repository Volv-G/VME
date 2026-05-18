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
from ..domain.events.player import (
    AceEvent,
    KillEvent,
    PlayerEvent,
)
from ..domain.events.serve import BallServedEvent
from ..domain.match import Match
from ..domain.roster import Roster
from ..domain.team import Team
from ..library import paths

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
    match: Match, home_roster: Roster, home_team_name: str
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
    total = match.total_frames()

    # Pre-compute global frames of all events once; saves O(N^2) scans
    # later when finding rally bounds for each highlight.
    indexed = list(_indexed_events(match))

    file_prefix = _match_file_prefix(match)

    out: list[BatchClip] = []
    for ev, g in indexed:
        if not isinstance(ev, PlayerEvent):
            continue
        start, end = _rally_bounds(indexed, g, fallback)
        start = max(0, start - pad_before)
        end = min(total, end + pad_after)
        if end <= start:
            continue

        player_part, team_part = _player_and_team_folders(
            ev.team, ev.player_number, home_roster, home_team_name, match
        )
        # Action label (e.g. "kill", "dig") becomes a subfolder so a
        # player's folder is grouped by action when browsed, AND appears
        # in the filename so files stay self-describing if they get
        # moved/flattened (e.g. dropped into a per-player archive across
        # matches). The clip filename also records WHEN the moment
        # happened in this match (date, opponent, in-match timestamp).
        action = ev.type_name
        ts = _format_timestamp(g, fps)
        rel = (
            f"highlights/{team_part}/{player_part}/{action}/"
            f"{file_prefix}_{ts}_{action}.mp4"
        )
        label = f"#{ev.player_number} {team_part} {action} @ {ts}"
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
    indexed: list[tuple], highlight_global: int, fallback_half: int
) -> tuple[int, int]:
    """Return (start_global, end_global) of the rally containing a frame.

    `indexed` is a list of `(event, global_frame)` tuples sorted by
    global_frame (events without a resolvable global frame already filtered
    out).

    Bounds:
      - rally start: nearest preceding `BallServedEvent` (g <= highlight).
      - rally end:
          * first `KillEvent` / `AceEvent` at g >= highlight (the scoring
            event itself - the rally ends *with* it, so when the highlight
            IS the kill we still want it included rather than excluded by
            a strict `>`); OR
          * first `BallServedEvent` at g > highlight (next rally's serve,
            used when no clean scoring event was logged).

    When no boundary exists on a side, we fall back to a symmetric
    half-window so the user still gets a watchable clip.
    """
    start: Optional[int] = None
    end: Optional[int] = None
    for ev, g in indexed:
        if g <= highlight_global and isinstance(ev, BallServedEvent):
            # Keep walking - we want the LAST such event before the highlight.
            start = g
            continue
        if isinstance(ev, (KillEvent, AceEvent)) and g >= highlight_global:
            end = g
            break
        if isinstance(ev, BallServedEvent) and g > highlight_global:
            end = g
            break

    if start is None:
        start = highlight_global - fallback_half
    if end is None:
        end = highlight_global + fallback_half
    return start, end


# ---------------------------------------------------------------------------
# Focused highlights
# ---------------------------------------------------------------------------


def focused_from_match(
    match: Match, home_roster: Roster, home_team_name: str
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
    file_prefix = _match_file_prefix(match)

    out: list[BatchClip] = []
    for in_ev, start, end in spans:
        player_part, team_part = _player_and_team_folders(
            in_ev.team,
            in_ev.player_number,
            home_roster,
            home_team_name,
            match,
        )
        start_ts = _format_timestamp(start, fps)
        end_ts = _format_timestamp(end, fps)
        # No action subfolder for focused clips - focus spans aren't
        # typed - but the same date_vs_opponent_timestamp naming makes
        # them consistent with the highlights output and meaningful
        # when flattened into a per-player archive across matches.
        rel = (
            f"focused/{team_part}/{player_part}/"
            f"{file_prefix}_{start_ts}-{end_ts}.mp4"
        )
        label = f"#{in_ev.player_number} {team_part} {start_ts}-{end_ts}"
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


def _player_and_team_folders(
    team: Team,
    jersey: int,
    home_roster: Roster,
    home_team_name: str,
    match: Match,
) -> tuple[str, str]:
    """Return `(player_subfolder, team_subfolder)` for filesystem use.

    Sanitized for filesystem safety. Unknown players just get the jersey
    number; the team folder falls back to a generic label when no
    metadata is available.
    """
    if team == Team.HOME:
        roster = home_roster
        team_label = home_team_name or "Home"
    else:
        roster = match.opponent_roster
        team_label = (
            match.opponent_roster.team_name or match.opponent or "Opponent"
        )
    player = roster.find_by_number(jersey)
    name = player.short_name or player.name if player else None
    return (
        paths.player_folder_name(jersey, name),
        paths.safe_segment(team_label),
    )


def _fps(match: Match) -> float:
    """Project FPS for pad arithmetic. Mirrors the renderer's choice of
    `clips[0].fps` first, `match.fps` second."""
    if match.clips and match.clips[0].fps > 0:
        return match.clips[0].fps
    return match.fps or 30.0


def _match_file_prefix(match: Match) -> str:
    """Build the `<date>_vs_<opponent>` filename prefix.

    Both segments are filesystem-sanitized via `paths.safe_segment` so
    free-form opponent names ("NW Jrs. 15 Blue") become safe leaves
    ("NW_Jrs._15_Blue"). Missing date / opponent fall back to readable
    placeholders rather than empty strings so we never produce names
    like `__03m22s.mp4`.
    """
    date = paths.safe_segment(match.date) if match.date else "no-date"
    opponent = (
        paths.safe_segment(match.opponent) if match.opponent else "TBD"
    )
    return f"{date}_vs_{opponent}"


def _format_timestamp(global_frame: int, fps: float) -> str:
    """Zero-padded `HHhMMmSSs` of an in-match frame position.

    Always emits hours so files sort chronologically inside a folder
    even for long matches (and so a 2 hour match's frame-1 clip doesn't
    sort *after* a 59 minute clip from the same match).
    """
    total_s = int(global_frame / max(fps, 1e-6))
    h = total_s // 3600
    m = (total_s % 3600) // 60
    s = total_s % 60
    return f"{h:02d}h{m:02d}m{s:02d}s"
