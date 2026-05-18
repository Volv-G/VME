"""Pydantic models for API request/response shapes."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class PlayerOut(BaseModel):
    name: str
    number: int
    short_name: Optional[str] = None
    profile_pic_path: Optional[str] = None


class RosterOut(BaseModel):
    team_name: Optional[str] = None
    team_color: Optional[str] = None
    players: list[PlayerOut] = Field(default_factory=list)


class TeamSummaryOut(BaseModel):
    name: str
    has_roster: bool
    tournament_count: int


class TournamentSummaryOut(BaseModel):
    team: str
    name: str
    match_count: int


class MatchSummaryOut(BaseModel):
    team: str
    tournament: str
    # Date folder under the tournament (canonical date string).
    date: str
    # Match folder leaf - the API/URL identifier for the match.
    name: str
    # 1-based match order on this date (None for legacy unnumbered folders).
    match_index: Optional[int] = None
    opponent: str
    clip_count: int
    has_match_json: bool
    has_video: bool


class ClipOut(BaseModel):
    id: str
    filename: str
    frame_count: int
    fps: float
    width: int
    height: int
    # Unix timestamp (UTC seconds) when the camera started recording this
    # clip. Used by the auto-cuts feature to detect adjacent recordings.
    start_recording_time: Optional[float] = None


class GameStateOut(BaseModel):
    """Snapshot of the volleyball game state after applying an event.

    Mirrors the subset of `domain.game_state.GameState` the UI needs to render
    score, current lineup, serving team and a few flags. Computed server-side
    by `Match._recompute_states()`; surfaced here so the UI doesn't need to
    re-derive (and risk diverging from the renderer).
    """

    home_score: int
    away_score: int
    home_sets: int
    away_sets: int
    serving_team: Optional[str] = None  # "home" | "away" | None
    home_positions: dict[int, Optional[int]] = Field(default_factory=dict)
    away_positions: dict[int, Optional[int]] = Field(default_factory=dict)
    game_started: bool = False
    game_ended: bool = False
    in_cut_region: bool = False
    ball_served_since_last_score: bool = False


class EventOut(BaseModel):
    type: str
    id: int
    payload: dict[str, Any] = Field(default_factory=dict)
    global_frame: Optional[int] = None
    state: Optional[GameStateOut] = None


class MatchOut(BaseModel):
    team: str
    tournament: str
    date: str
    name: str
    # 1-based match order on this date (None for legacy unnumbered folders).
    match_index: Optional[int] = None
    opponent: str
    fps: float
    clips: list[ClipOut]
    events: list[EventOut]
    home_roster: RosterOut
    opponent_roster: RosterOut


class CreateTournamentIn(BaseModel):
    name: str


class CreateMatchIn(BaseModel):
    """New-match request. The folder is `<Tournament>/<date>/<NN>_<opponent>/`
    where `NN` is the 1-based match order on that date. If `match_index` is
    omitted, the server picks the next available number for that date."""

    opponent: str
    date: str
    match_index: Optional[int] = None


class UpdateMatchIn(BaseModel):
    opponent: Optional[str] = None
    date: Optional[str] = None
    fps: Optional[float] = None
    opponent_roster: Optional[RosterOut] = None


class ReorderClipsIn(BaseModel):
    clip_ids: list[str]


class EventCreateIn(BaseModel):
    type: str
    clip_id: Optional[str] = None
    local_frame: Optional[int] = None
    payload: dict[str, Any] = Field(default_factory=dict)


class EventUpdateIn(BaseModel):
    clip_id: Optional[str] = None
    local_frame: Optional[int] = None
    payload: dict[str, Any] = Field(default_factory=dict)


class AutoCutsIn(BaseModel):
    """Options for the auto-cuts run.

    `has_intro_clip` tells the server that clip 0 is a title / intro reel
    rather than gameplay. When true, the GameStart event is anchored to
    the start of clip 1 (with a fade-in from black) and the normal
    cut/crossfade between clips 0 and 1 is suppressed so the fade-in is
    the only transition the viewer sees.
    """

    has_intro_clip: bool = False


class AutoCutsOut(BaseModel):
    """Result of an auto-cuts run.

    `match` is the updated match (so the UI can apply it without an extra
    GET). The counters explain how many boundaries were processed.
    """

    match: MatchOut
    # Number of crossfade cut pairs (CutStart + CutEnd) inserted.
    added: int
    # Number of SetEnd lifecycle events inserted at inter-set gaps.
    added_set_ends: int
    # GameStart / GameEnd lifecycle events inserted by this run (0 or 1
    # each - the events are idempotent so re-running auto-cuts won't keep
    # adding more).
    added_game_start: int = 0
    added_game_end: int = 0
    skipped_existing: int
    skipped_missing_time: int
    skipped_too_short: int
    # Continuous-recording pairs (gap below min) - hard cut is already
    # invisible, so no crossfade is needed.
    skipped_too_close: int


class RenderFileOut(BaseModel):
    """One rendered output file on disk under `<match>/renders/`."""

    filename: str
    size_bytes: int
    # File mtime as a unix timestamp (seconds). Used as a creation proxy;
    # render files are never modified after the job finishes.
    created_at: float


class RenderRequestIn(BaseModel):
    """Start a render. If `playhead_frame` is set, only a window around it
    is rendered (preview); otherwise the whole match renders."""

    label: Optional[str] = None
    # Render kind. Defaults to "full"; clients can request "highlights"
    # or "focused_highlights" for batch jobs (one mp4 per detected span).
    kind: Optional[str] = None
    # Source/global frame around which to render a preview window. None for full render.
    playhead_frame: Optional[int] = None
    # Half-window length in seconds (so the preview is 2*seconds_around long).
    # Only used when playhead_frame is set.
    seconds_around: Optional[float] = None
    # When True, run the job immediately in its own thread instead of
    # waiting for the dispatcher. Intended for short previews where the
    # user wants instant feedback - long full renders should always be
    # queued so they don't tie up the machine mid-edit.
    immediate: bool = False
