"""Event effects: how a `MatchEvent` influences each output dimension.

An event declares its effects via four properties, any of which may return
`None`. The four dimensions are:

  - `timeline_effect`  - cuts/transitions/fades on the rendered timeline
  - `overlay_effect`   - what the renderer draws on top of frames
  - `score_effect`     - score deltas applied to game state
  - `roster_effect`    - on-court roster / serving / rotation changes

Effects are plain data; logic lives on the event itself or in the renderer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .team import Team


# ----- Timeline -------------------------------------------------------------


@dataclass(frozen=True)
class TimelineEffect:
    """Unified parameterization of all timeline transitions.

    fade_frames:
        Length of the color-blend ramp on each side of the boundary.
        Section A's tail ramps `color_weight` 0->1 over this many frames;
        Section B's head ramps 1->0 symmetrically (when `frame_shift` overlaps
        them).
    frame_shift:
        Frames to shift section B relative to section A's join point.
        - 0:                 fade with a gap (fade out, hold, fade in)
        - -fade_frames:      crossfade (sections overlap exactly through the ramp)
        - other values:      partial overlap or extended gap.
    blend_color:
        RGB color to blend toward (default black).
    """

    fade_frames: int = 0
    frame_shift: int = 0
    blend_color: tuple[int, int, int] = (0, 0, 0)


# ----- Overlay --------------------------------------------------------------


@dataclass(frozen=True)
class ScoreboardVisibilityEffect:
    """Show or hide the scoreboard overlay starting at this event."""

    visible: bool


@dataclass(frozen=True)
class PlayerPopupEffect:
    """A popup tied to a player (e.g. for a kill, ace, sub).

    `text` is the title line shown in big type (the action label - "Kill",
    "Sub", etc.). When `player_number` is set, the renderer also shows a
    subtitle with the jersey number and name resolved from the roster.
    When `team` is set and `bg_color` is None, the renderer tints the popup
    with that team's color so home/away events are visually distinct.
    """

    text: str
    player_number: Optional[int] = None
    team: Optional[Team] = None
    image_path: Optional[str] = None
    bg_color: Optional[str] = None  # explicit hex override; else from team


@dataclass(frozen=True)
class MessagePopupEffect:
    """Freeform message popup (annotation)."""

    text: str
    image_path: Optional[str] = None


OverlayEffect = ScoreboardVisibilityEffect | PlayerPopupEffect | MessagePopupEffect


# ----- Score ----------------------------------------------------------------


@dataclass(frozen=True)
class PointScoredEffect:
    """A team scored a single point (with rotation logic on sideout)."""

    team: Team


@dataclass(frozen=True)
class ScoreDeltaEffect:
    """Manual score correction; deltas may be negative."""

    home_delta: int = 0
    away_delta: int = 0


@dataclass(frozen=True)
class ScoreResetEffect:
    """Set scores to 0:0 (used at end of set)."""


ScoreEffect = PointScoredEffect | ScoreDeltaEffect | ScoreResetEffect


# ----- Roster ---------------------------------------------------------------


@dataclass(frozen=True)
class ServeTeamSetEffect:
    """Designate which team has serve (used for first serve of a set)."""

    team: Team


@dataclass(frozen=True)
class SubstitutionApplyEffect:
    """Replace the player at a given court position."""

    team: Team
    position: int
    player_in: int


@dataclass(frozen=True)
class PositionsResetEffect:
    """Clear all positions for both teams (end of set)."""


@dataclass(frozen=True)
class BallServedEffect:
    """Mark that a ball has been served (validation / state flag only)."""


@dataclass(frozen=True)
class ReplayMarkerEffect:
    """Reset the ball-served flag (rally restarted / replayed point)."""


RosterEffect = (
    ServeTeamSetEffect
    | SubstitutionApplyEffect
    | PositionsResetEffect
    | BallServedEffect
    | ReplayMarkerEffect
)
