"""Pydantic models for API request/response shapes."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class PlayerOut(BaseModel):
    name: str
    number: int
    short_name: Optional[str] = None
    profile_pic_path: Optional[str] = None


class YouTubeConfigOut(BaseModel):
    """Team-level YouTube upload defaults.

    Round-trips through roster.json. All fields are optional on input;
    server-side defaults kick in for omitted keys (see
    `domain/roster.py::YouTubeConfig.from_dict`).
    """

    privacy_status: str = "unlisted"
    playlist_id: Optional[str] = None
    title_template: Optional[str] = None
    description_template: Optional[str] = None


class NamingConfigOut(BaseModel):
    """Team-level render-output naming templates.

    All fields optional on input - server fills in defaults that match
    the legacy hard-coded paths (see `app/render/naming.py`).
    """

    full_render_template: Optional[str] = None
    highlight_template: Optional[str] = None
    focused_template: Optional[str] = None


class RosterOut(BaseModel):
    team_name: Optional[str] = None
    team_color: Optional[str] = None
    youtube: Optional[YouTubeConfigOut] = None
    naming: Optional[NamingConfigOut] = None
    players: list[PlayerOut] = Field(default_factory=list)


class TournamentInfoOut(BaseModel):
    """Display metadata for a tournament (the `tournament.json` sidecar).

    Both fields optional; missing values fall back to the folder slug
    pretty-printed (`_` -> space) on the server side.
    """

    abbreviation: Optional[str] = None
    full_name: Optional[str] = None


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


class FullRenderOut(BaseModel):
    """A full-match render listed on the team dashboard.

    Carries the location (team/tournament/date/match/filename) needed to
    download / upload, plus the YouTube upload state if a sidecar exists
    for this file.
    """

    team: str
    tournament: str
    date: str
    match: str
    filename: str
    size_bytes: int
    created_at: float
    opponent: str = ""
    match_index: Optional[int] = None
    youtube_video_id: Optional[str] = None
    youtube_uploaded_at: Optional[float] = None
    # `youtube_privacy_status` is what the video is set to *right now*
    # on YouTube (read back from the upload response and persisted in
    # the sidecar). May differ from `youtube_requested_privacy_status`
    # when Google silently downgraded the upload - the UI uses the
    # mismatch to flag the row.
    youtube_privacy_status: Optional[str] = None
    youtube_requested_privacy_status: Optional[str] = None


class UploadYouTubeRequestIn(BaseModel):
    """Request body for enqueueing a YouTube upload of a rendered file.

    `filename` is the leaf inside the match's `renders/` folder (no
    subfolders allowed - only full renders are uploadable). Templates
    on the team profile produce title/description by default; the
    optional overrides here let the user tweak before submitting.
    """

    filename: str
    title_override: Optional[str] = None
    description_override: Optional[str] = None
    privacy_override: Optional[str] = None
    playlist_override: Optional[str] = None


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


class UploadSessionStartIn(BaseModel):
    """Body for `POST .../clips/uploads` (begin a chunked upload).

    `filename` is the source basename - the server may rename to avoid
    collisions and reports the resolved name back via `UploadSessionOut`.
    `total_size` is the full source length in bytes; the server uses it
    to validate that chunks don't overrun and to decide when finalize
    is allowed.
    """

    filename: str
    total_size: int


class UploadSessionOut(BaseModel):
    """Server view of a chunked-upload session.

    Returned by session-create, chunk-append, get, in roughly the same
    shape so the client can poll the same model after a network blip
    to recover its current offset.
    """

    session_id: str
    filename: str
    total_size: int
    received: int


class ImportPathIn(BaseModel):
    """Body for `POST .../clips/import-path` (server-side ingest).

    `source_path` must be an absolute path on the server's filesystem.
    May point at a single video file or a directory of them.
    `mode` is "copy" (default, non-destructive) or "move" (which uses
    `shutil.move` - rename when same-volume, copy+delete when across).
    """

    source_path: str
    mode: str = "copy"  # "copy" | "move"


class ImportPathOut(BaseModel):
    """Result of a path-import. `imported` is the list of destination
    basenames added to the match folder (collision-resolved). `match`
    is the updated match so the UI can refresh without an extra GET.
    """

    imported: list[str]
    match: MatchOut


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
