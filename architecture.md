# VME — Architecture

Developer-oriented complement to [README.md](README.md). The README
explains how to run and deploy; this file explains how the app is put
together.

## 1. What it is

A web-based volleyball match editor. Two coupled jobs:

1. **Tag** a match — load source video clips, scrub the timeline, log
   events (kills, aces, substitutions, set boundaries, focus spans...)
   at frame-accurate positions.
2. **Render** the tagged match into one or more mp4 outputs (full match
   with scoreboard, per-player highlight reels, focused-player cuts).
   Optionally upload the result to YouTube.

A single user session edits one `<Team>/<Tournament>/<Date>/<Match>` at
a time. There's no multi-user collaboration model.

## 2. Tech stack

| Layer | Choice | Why |
|---|---|---|
| Backend | Python 3.11 + FastAPI | FastAPI gets us SSE + async cleanly; same Python toolbox as the legacy desktop app |
| Render | MoviePy + Pillow + ffmpeg | Reused from the legacy desktop app's render pipeline |
| Frontend | React + Vite + TypeScript | Standard SPA stack; no SSR needed |
| Storage | Filesystem + JSON sidecars | Folders are user-readable; no DB to maintain; trivial backup by copying the tree |
| Auth | HTTP Basic (single shared user) | Single-tenant tool |
| Process supervisor | NSSM (Windows service) | Bog-standard Windows service wrapper |
| TLS / front door | IIS reverse-proxy *or* Hypercorn standalone | Both supported; IIS is recommended when the host already runs IIS |
| YouTube upload | `google-api-python-client` + Desktop OAuth | Lazy-imported so the app boots without the libs |

## 3. Repository layout

```
backend/
  app/
    api/              FastAPI routers (one file per resource)
      clips.py        upload (chunked + path-import), reorder, delete, auto-cuts
      events.py       event CRUD
      matches.py      match CRUD (no /rescan; GET implicitly reconciles)
      media.py        clip + render file streaming
      renders.py      render request, YouTube upload request, status
      teams.py        team + roster + naming + YouTube config
      tournaments.py  per-tournament info sidecar
      schemas.py      Pydantic DTOs shared by all routers
      helpers.py      load_match_or_404 + shared serialization
    domain/           Pure data; no I/O
      match.py        Match, Clip aggregate
      events/         MatchEvent hierarchy (one file per concrete event)
      game_state.py   GameState snapshot
      roster.py       Roster + YouTubeConfig + NamingConfig
      tournament.py   Tournament info (abbreviation, full_name)
      effects.py      Timeline / overlay / score / roster effect dataclasses
      player.py       Player dataclass
      team.py         Team enum (HOME / AWAY)
    library/          Filesystem + tooling
      scanner.py      Walks the media tree; loads/creates/reconciles
      paths.py        Match folder paths, tournament.json path, sidecars
      probe.py        ffprobe wrapper -> VideoInfo
      transcode.py    ffmpeg fps-normalize on upload
      ffmpeg_tools.py Resolves ffmpeg/ffprobe (VME_FFMPEG / VME_FFPROBE + autodetect)
    render/
      renderer.py     Full-match MatchRenderer (MoviePy + overlays)
      frame_map_builder.py  Per-output-frame metadata (which clip, overlays, blends)
      batch.py        Highlight + focused-player batch renderers
      naming.py       Variable builders + render_naming_template + sanitization
    jobs/
      manager.py      In-memory queue + persistence (jobs.json)
      dispatcher.py   Single-worker pump that runs RenderJob -> render or upload
      render_job.py   Concrete handlers for kind="render" and kind="youtube_upload"
      persistence.py  jobs.json read/write
    upload/
      youtube.py      OAuth + resumable upload + playlist attach
      templates.py    Title / description template expansion
    config.py         DATA_DIR / MEDIA_ROOT / LOG_ROOT / CERT_DIR resolution
    auth.py           HTTP Basic Auth middleware
    main.py           App wiring (router includes, middleware, static fallback)
    serve.py          Standalone Hypercorn entrypoint
  scripts/
    yt_authorize.py   One-time YouTube OAuth flow
  pyproject.toml
frontend/
  src/
    routes/           Page-level components (router targets)
    components/       Reusable UI: VideoPlayer, Timeline, Controls, ClipManager, ...
      cutAnalysis.ts  Orphan cut_start/cut_end detection (red badges)
      focusAnalysis.ts Orphan FocusIn detection (no PlayerEvent in rally)
    hotkeys/          Generic hotkey registry + React hook
    api/              Typed fetch wrapper (chunked uploader lives here)
    types/            DTOs mirroring backend pydantic models
  public/             Static assets copied verbatim into the build (favicon, etc.)
iis/                  web.config used when deployed behind IIS
scripts/              PowerShell setup / install / OAuth scripts
```

User content lives outside the repo, at the path named by `VME_DATA_DIR`
(or PROJECT_ROOT as a fallback).

## 4. Data storage

No database. JSON sidecars in a filesystem tree:

```
<data-dir>/
  media/<Team>/                   roster.json
    <Tournament>/                 tournament.json (optional)
      <YYYY-MM-DD>/
        <NN_Opponent>/            match.json
                                  <source>.mp4 ...
                                  <render outputs>.mp4
                                  <render outputs>.mp4.youtube.json
  logs/service.log
  jobs.json                       render queue persistence
  youtube_client_secret.json
  youtube_token.json
```

Three sidecar flavors:

- **`roster.json`** — per team. Players, team color, `youtube` config
  block, `naming` config block. The team-admin settings (`youtube`,
  `naming`) live only here; the per-match copy of the opponent's roster
  in `match.json` explicitly excludes them.
- **`tournament.json`** — optional, per tournament. Carries
  `abbreviation` and `full_name` overrides used by render-output naming
  templates. Falls back to folder-slug derivations when absent.
- **`match.json`** — per match. `clips`, `events`, opponent name + date
  + fps, plus a stripped-down `opponent_roster` (team name, team color,
  players — no admin junk).
- **`<file>.mp4.youtube.json`** — sidecar next to any rendered mp4 that
  has been uploaded. Records the YouTube video URL + upload timestamp.

Writes go through `Match.save()` / `Roster.save()` / `Tournament.save()`
(JSON, indented, hand-editable). Every UI mutation is an explicit API
call — the editor never edits files behind the user's back.

Filesystem-as-database trades referential safety for human operability:
a coach can copy a folder onto a thumb drive, hand-edit a misspelled
name in JSON, drop a new clip into the folder, etc.

## 5. Domain model

```
Match (domain/match.py)
  opponent: str
  date: str
  fps: float
  clips: list[Clip]                  # ordered
  events: list[MatchEvent]           # always sorted by global frame
  opponent_roster: Roster            # stripped (no youtube/naming)

Roster (domain/roster.py)
  team_name, team_color
  players: list[Player]
  youtube: YouTubeConfig             # only on the team's own roster.json
  naming: NamingConfig                # ditto

Tournament (domain/tournament.py)
  abbreviation: Optional[str]
  full_name: Optional[str]
```

The home roster is **not** in `Match` — it's at `<Team>/roster.json`
and shared across all matches for that team. Each match carries a copy
of the opponent's roster (per-match, since opponents differ).

### 5.1 Frame indexing

Frames are stored *clip-relative* on every event:

```python
@dataclass
class MatchEvent:
    clip_id: str
    local_frame: int
    state: Optional[GameState]   # computed, not persisted
```

Reordering clips, deleting a clip, or inserting a new one never needs
to touch any event's frame number. The match resolves global frames on
demand:

```python
match.to_global_frame(clip_id, local_frame)   # local -> global
match.from_global_frame(global_frame)         # global -> (clip_id, local)
```

`ClipTransitionEvent` is the one exception: it sits at a clip boundary,
not a specific frame, and is sorted via the offset of `from_clip_id`.

### 5.2 Event class hierarchy

`MatchEvent` (base) → dataclass subclasses for each concrete event
(`KillEvent`, `SubstitutionEvent`, `BallServedEvent`, `FocusInEvent`,
`GameStartEvent`, ...). Subclasses are registered in
`domain/events/registry.py` and listed in `domain/events/__init__.py`.
Each subclass declares its impact through four optional properties:

| Property | Says... | Examples |
|---|---|---|
| `timeline_effect` | how the rendered timeline cuts/fades around this event | cut_end (with fade_frames, frame_shift), set_end, game_start, game_end |
| `overlay_effect` | what the renderer draws on top of frames | scoreboard show/hide, player popup, message popup |
| `score_effect` | how the score changes | PointScored, ScoreDelta, ScoreReset |
| `roster_effect` | how the on-court lineup / serving team change | Substitution, ServeTeamSet, BallServed, ReplayMarker |

The base `apply()` walks these effects and mutates `GameState` without
subclasses needing to write per-event apply logic. Subclasses override
only when they need custom semantics.

### 5.3 Game state

`GameState` (`domain/game_state.py`) is a value-typed snapshot:

```
home_score, away_score, home_sets, away_sets
serving_team
home_positions: {1..6 -> jersey | None}
away_positions: {1..6 -> jersey | None}
game_started, game_ended, in_cut_region, ball_served_since_last_score
```

Whenever `Match.events` changes, `_recompute_states()` walks all events
in order, calls `event.apply(state, all_events)` and stores the
resulting `GameState` snapshot on `event.state`. The renderer reads
`event.state` directly; the UI does too (see §7).

`event.state` is **not** persisted to `match.json` — it's deterministic
output of replaying events through `apply()` and is re-derived every
load.

## 6. Render pipeline

Three render flavors share one frame-map builder and one overlay stack:

```
build_frame_map(match) -> FrameMap         # one entry per output frame
MatchRenderer(match, media_dir, home, away)
  .render(out_path, progress_cb)           # MoviePy VideoClip.write_videofile

batch.highlights_from_match(...)           # per-player highlight reels
batch.focused_from_match(...)              # focused-player cuts (FocusIn / FocusOut spans)
```

`FrameMap` answers, for each output frame: which clip is reading, which
local frame, what overlays are visible at this moment, what color blend
is in effect (mid-fade), etc. `MatchRenderer` is then a stateless
function: "give me the pixel buffer for output frame N."

Overlays:

- `ScoreboardOverlay` reads `event.state` to know score/sets/lineup at
  any frame, and uses `TeamBranding` (name + color) for both teams.
- `MessageOverlayRenderer` draws transient text/image popups for
  `message` events.

### 6.1 Output naming

`render/naming.py` resolves the per-team template at enqueue time:

```python
vars_ = build_full_render_vars(match, ..., tournament_info=..., naming_config=...)
rel_path = render_naming_template(home_roster.naming.full_render_template, vars_)
```

Templates can contain slashes (interpreted as subfolders). Variable
values are sanitized via `paths.safe_segment`; the literal template
text is trusted (you wrote it). `..` segments in the output are
rejected. On template error the full-render job falls back to the
default template; batch (highlight/focused) jobs log a warning and skip
the offending clip.

For focused clips, `{action}` is computed in `render/batch.py::
_first_action_for_player_in_rally`. Rally bounds reuse the same
heuristic as highlights (nearest preceding `BallServed` to next
`BallServed` / `Kill` / `Ace`); the action label is the type_name of
the first PlayerEvent by the focused player inside that rally. Spans
with no matching player event are skipped (and flagged red in the UI;
see §9).

### 6.2 Job queue

Renders run as background jobs through `jobs/dispatcher.py` (single
worker; one concurrent render at a time). The dispatcher pulls
`RenderJob`s from a `RenderJobManager` queue and dispatches them based
on `job.kind`:

| `kind` | Handler | Notes |
|---|---|---|
| `"render"` | `_run_render` | Full match (`MatchRenderer`) or batch (`highlights_from_match` / `focused_from_match`) depending on `payload.batch`. |
| `"youtube_upload"` | `_run_upload` | Resumable upload, 8 MiB chunks, retries on 5xx with exponential backoff, optional playlist attach (best-effort). |

Job state persists to `<data-dir>/jobs.json` so a service restart picks
up where it left off (well: queued jobs survive; in-flight ones move to
`cancelled`). Progress is streamed to the UI via Server-Sent Events on
`/api/jobs/{id}/events`.

### 6.3 YouTube upload

`app/upload/youtube.py` wraps the resumable upload protocol. Key
points:

- **Lazy imports** — `google-api-python-client` and friends are only
  imported when an upload is actually triggered, so the service boots
  fine without them installed.
- **Server-level "configured" check** is a 3-way AND: library
  installed, `youtube_client_secret.json` present, `youtube_token.json`
  present. Surfaced via `GET /api/youtube/status` and reflected in the
  Team dashboard's YouTube card.
- **Templates resolved at enqueue, not at render** — once a job is
  queued, editing the team's YouTube template doesn't retroactively
  rewrite its title/description.
- **Playlist attach failure is a warning, not a job failure** — a
  typo'd playlist_id shouldn't waste hours of upload.
- **Cancel between chunks** — the dispatcher's cancel signal is checked
  at every 8 MiB chunk boundary.

## 7. State sync between UI and server

**The server is the single source of truth; the UI never re-derives
anything the server has already computed.**

### Mutation flow

```
UI button click
  -> POST/PATCH/DELETE /api/.../events    (fetch wrapper in api/client.ts)
  -> Match.add_event / update_event / delete_event
       -> _sort_events()
       -> _recompute_states()
       -> Match.save() (writes match.json)
  -> serialize_match(...) returns full MatchOut
  -> UI replaces local data state with the response
```

Every event mutation refetches the entire match. Cheap (events JSON is
tiny) and avoids partial-update bugs.

### State at the playhead

`EventOut.state: Optional[GameStateOut]` is serialized for every event
(except `ClipTransitionEvent`). This is the GameState **after** that
event applied. The UI looks up "live" state with one line:

```ts
// frontend/src/components/Controls/state.ts
events.findLast(e => e.global_frame !== null && e.global_frame <= currentFrame)
  ?.state
  ?? EMPTY_STATE;
```

No replay loop in the frontend. No risk of UI/render divergence on
subtle rules (`max(0, ...)` clamping in score corrections, point-scored
rotation, ball-served reset, sets accumulation, etc.).

## 8. API surface

All routes under `/api`. The match-resource path includes the full
tournament hierarchy:

```
/api/teams/{team}/tournaments/{tournament}/dates/{date}/matches/{match}
```

Response models live in `backend/app/api/schemas.py`. Mutations to
`clips` or `events` always return the full `MatchOut`.

| Path | Verbs | Purpose |
|---|---|---|
| `/api/health` | GET | unauthed health probe (reports media_root + data_dir) |
| `/api/teams` | GET, POST | list / create team |
| `/api/teams/{team}/roster` | GET, PUT | get / update roster (incl. youtube + naming config) |
| `/api/teams/{team}/full-renders` | GET | aggregated list of all completed full renders (for the team dashboard) |
| `/api/teams/{team}/tournaments` | GET, POST | list / create tournament |
| `/api/teams/{team}/tournaments/{tournament}` | DELETE | remove a tournament (folder must be empty) |
| `/api/teams/{team}/tournaments/{tournament}/info` | GET, PUT | tournament abbreviation + full_name |
| `/api/teams/{team}/tournaments/{tournament}/matches` | GET, POST | list / create match |
| `.../matches/{match}` | GET, PATCH, DELETE | match metadata + opponent_roster; GET implicitly rescans the folder |
| `.../matches/{match}/clips` | PUT | reorder clips |
| `.../matches/{match}/clips/{id}` | DELETE | delete clip |
| `.../matches/{match}/clips/auto-cuts` | POST | run auto-cuts (GameStart/End + crossfades + set-ends) |
| `.../matches/{match}/clips/uploads` | POST | begin chunked upload session |
| `.../matches/{match}/clips/uploads/{sid}` | GET, DELETE | inspect / cancel session |
| `.../matches/{match}/clips/uploads/{sid}/chunk?offset=N` | PUT | append a chunk |
| `.../matches/{match}/clips/uploads/{sid}/finalize` | POST | promote .part to final + ingest |
| `.../matches/{match}/clips/import-path` | POST | server-side path import (copy or move) |
| `.../matches/{match}/media/clips/{id}/stream` | GET | range-supporting clip stream |
| `.../matches/{match}/media/renders/{file:path}` | GET, DELETE | download / delete a rendered file (path includes subfolders) |
| `.../matches/{match}/events` | GET, POST | list / create event |
| `.../matches/{match}/events/{id}` | PATCH, DELETE | edit / delete event |
| `.../matches/{match}/renders` | POST | enqueue render job (full / highlights / focused) |
| `.../matches/{match}/uploads/youtube` | POST | enqueue YouTube upload (body names the source render file) |
| `/api/jobs` | GET | list jobs (filterable) |
| `/api/jobs/{id}` | GET | poll one job |
| `/api/jobs/{id}/events` | GET (SSE) | stream progress |
| `/api/jobs/{id}` | DELETE | cancel a queued / running job |
| `/api/queue/state` | GET | active counts, queue depth, dispatcher state |
| `/api/youtube/status` | GET | { configured, lib_installed, client_secret_present, token_present } |

### 8.1 Chunked upload protocol

```
POST   .../clips/uploads          {filename, total_size}
                                  -> {session_id, filename, total_size, received: 0}
PUT    .../clips/uploads/{sid}/chunk?offset=N
                                  body = raw octet-stream (one chunk)
                                  -> {session_id, ..., received: M}
POST   .../clips/uploads/{sid}/finalize
                                  -> MatchOut
DELETE .../clips/uploads/{sid}    -> {cancelled: true}
GET    .../clips/uploads/{sid}    -> session state (for resume)
```

Sessions live in-memory (`_sessions: dict[str, _UploadSession]`) and
write to `.upload-{sid}.{filename}.part` files in the match folder.
Chunk offsets must arrive in order; the offset query parameter is
compared against the current `received` count, so duplicate retries and
skipped-chunk gaps are both rejected with 409. The session-create step
opportunistically purges sessions older than 6 hours.

The frontend (`api/client.ts::uploadClip`) defaults to 32 MiB chunks
and provides an `AbortSignal` so cancel cleans up the server session.

## 9. Frontend architecture

### Pages

`TeamSelectPage` → `TeamDashboardPage` → `TournamentDashboardPage` →
`MatchEditorPage`. Routing via `react-router-dom`.

### Match editor layout

Three columns on the top row, full-width timeline below:

```
+--------------------------+----------+----------+
| VideoPlayer              | Controls | Events   |
+--------------------------+----------+----------+
| splitter (drag to resize)                      |
+------------------------------------------------+
| Timeline (full width, scrolls horizontally)    |
+------------------------------------------------+
```

- **VideoPlayer** ([components/VideoPlayer.tsx](frontend/src/components/VideoPlayer.tsx))
  - HTML5 `<video>` wrapped in `react-zoom-pan-pinch`.
  - Treats all clips as one continuous stream (auto-advances on `ended`,
    cross-clip frame stepping, end-of-timeline rewind).
  - Robust frame-stepping (`frameFromTime` / `timeForFrame`) absorbs
    keyframe-snapping browser behavior.
- **Timeline** ([components/Timeline/Timeline.tsx](frontend/src/components/Timeline/Timeline.tsx))
  - Read-only canvas rendering: clip bars, event pins, cut regions,
    playhead, smart-spaced ticks.
  - Vertical wheel = zoom around cursor; shift / horizontal wheel = pan.
  - Zoom is clamped to keep the backing canvas under the 16384 px
    per-axis WebGL/HTML-canvas limit.
  - Click anywhere = seek + select the globally-closest event. Click
    inside an event pin's 8 px hit band = select that event precisely
    (no seek).
- **ControlsPanel** ([components/Controls/](frontend/src/components/Controls/))
  - Score, lineup grid, +1 buttons, action button matrix.
  - Reads `stateAtPlayhead(events, currentFrame)` for everything; never
    walks events for itself.
  - "Two-step picker": click a player-action button, then click a
    position. `ace` is a special case (auto-uses home P1 server).
    Kill -> follow-up assister picker (same frame, separate AssistEvent).
- **EventList** ([components/EventList.tsx](frontend/src/components/EventList.tsx))
  - One row per event with a human summary built by
    [`summarizeEvent`](frontend/src/components/eventSummary.ts).
  - Orphan detection: `cutAnalysis.ts` flags unpaired `cut_start` /
    `cut_end` events; `focusAnalysis.ts` flags FocusIn events whose
    rally has no PlayerEvent for the focused player. Both surface as a
    red `!` badge with a specific tooltip, and as a red ring on the
    Timeline pin.
- **ClipManager** ([components/ClipManager.tsx](frontend/src/components/ClipManager.tsx))
  - Two upload paths: "Upload videos" (chunked, with per-row ✕ cancel
    and ↻ retry) and "Import from path…" (server-side ingest, copy or
    move). Implicit rescan on every match refresh — no separate
    rescan button.
- **TeamYouTubeSettings**, **TeamNamingSettings**, **TeamFullRendersPanel**
  - Team dashboard cards. The full-renders panel is polled (5 s) and
    shows an "Upload to YouTube" button per render (disabled with
    tooltip when the server isn't OAuth-configured) or a "▶ on YouTube"
    link if the render already has a sidecar.

### Hotkey system

Decoupled from any specific component:

```
hotkeys/
  catalog.ts      static ACTION_CATALOG + DEFAULT_BINDINGS
  registry.ts     singleton: handler map, bindings (localStorage), dispatcher
  combos.ts       KeyboardEvent.code -> canonical combo string
  useHotkeyAction React hook that registers a handler by action id
```

Components register handlers by id
(`useHotkeyAction("playback.play1x", () => playAt(1))`). A future
configurator UI mutates `bindings` only; component code never moves.
Combo strings are built from `e.code` so they're keyboard-layout
independent.

## 10. Deployment

Two modes (see README for setup):

- **Mode A (recommended): IIS reverse proxy** — IIS terminates TLS on
  :443, ARR proxies `https://host/vme/*` to the Python service on
  `127.0.0.1:8000`. Frontend is built with `BASE_PATH=/vme/`. SSE works
  because ARR response buffering is explicitly disabled in
  [iis/web.config](iis/web.config). Large clip uploads work because
  `setup-iis.ps1` raises `uploadReadAheadSize` at the applicationHost
  level (the section is locked at parent level so it can't go in
  web.config).
- **Mode B: Hypercorn standalone** — `serve.py` binds :80 (HTTP→HTTPS
  redirect) and :443 (TLS) directly. Used when IIS isn't available.

In both modes the backend is wrapped as a Windows service via NSSM,
configured in `scripts/install-service.ps1`. The install script
allow-lists a specific set of env vars from `secrets.env` into the
service process; `PYTHONHOME` and `PYTHONPATH` are explicitly cleared
to prevent a system-wide Python install (e.g. 3.14) from shadowing the
venv's 3.11.

## 11. Auth

`BasicAuthMiddleware` in `backend/app/auth.py`. One shared username +
bcrypt hash, both pulled from environment (`secrets.env`). The
middleware is added *before* CORS so auth runs first; OPTIONS
preflights are whitelisted. Every API path requires auth except
`/api/health`.

This is intentionally simple. The tool is single-tenant; if it ever
needs real auth, swap this middleware out.

## 12. Logs

- `<data-dir>/logs/service.log` — NSSM-captured stdout + stderr with
  5 MiB rotation.
- `<data-dir>/logs/backend.log` — per-process backend logger (renders
  emit per-frame progress at INFO level).

`<data-dir>` defaults to PROJECT_ROOT when `VME_DATA_DIR` is unset.
The install script prints the resolved path at install time precisely
because misconfiguration here used to silently split state between two
directories.
