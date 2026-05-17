# VME - Architecture

This document is the developer-oriented complement to [README.md](README.md).
The README explains how to run and deploy; this file explains how the app is
put together.

## 1. What it is

A web-based volleyball match editor. Two coupled jobs:

1. **Tag** a match: load source video clips, scrub the timeline, log events
   (kills, aces, substitutions, set boundaries...) at frame-accurate
   positions.
2. **Render** the tagged match into an mp4 with a scoreboard and
   contextual popups baked in.

A single user session edits one `<Team>/<Match>` at a time. There's no
multi-user collaboration model.

## 2. Tech stack

| Layer | Choice | Why |
|---|---|---|
| Backend | Python 3.11 + FastAPI | Same Python toolbox as the legacy desktop app; FastAPI gets us SSE + async cleanly |
| Render | MoviePy + Pillow + ffmpeg | Reused from the legacy desktop app's render pipeline |
| Frontend | React + Vite + TypeScript | Standard SPA stack; no SSR needed |
| Storage | Filesystem (`media/<Team>/<Match>/...`) + JSON sidecars | Folders are user-readable; no DB to maintain; trivial to back up by copying the tree |
| Auth | HTTP Basic (single shared user) | Single-tenant tool; sufficient for a small trusted group |
| Process supervisor | NSSM (Windows service) | Bog-standard Windows service wrapper |
| TLS / front door | IIS reverse-proxy *or* Hypercorn standalone | Both supported; IIS is the recommended path on a machine that already runs IIS |

## 3. Repository layout

```
backend/         FastAPI app, domain model, render pipeline
  app/
    api/         FastAPI routers (one file per resource)
    domain/      Match + events + GameState (pure data, no I/O)
    library/     Filesystem scanner, ffprobe wrapper, path helpers
    render/      MoviePy renderer + overlay renderers + frame map builder
    jobs/        Background render-job manager (in-process)
    main.py      App wiring (middleware, static fallback, mounts)
    auth.py      HTTP Basic Auth middleware
    serve.py     Standalone Hypercorn entrypoint (port 80 redirect + 443 TLS)
  pyproject.toml
frontend/        Vite + React + TS SPA
  src/
    routes/      Page-level components (router targets)
    components/  Reusable UI: VideoPlayer, Timeline, Controls, ...
    hotkeys/     Generic hotkey registry + React hook
    api/         Typed fetch wrapper
    types/       DTOs mirroring backend pydantic models
iis/             web.config used when deployed behind IIS
scripts/         PowerShell setup/install scripts
media/           User content (gitignored): teams, matches, clips, renders
```

## 4. Data storage

No database. Two JSON sidecar files per material:

```
media/
  <Team>/
    roster.json                 # full home roster + team color
    <Match>/
      <whatever>.mp4            # one or more source clips
      match.json                # opponent meta + clip list + events
      renders/
        preview_*.mp4
```

`match.json` is the only mutable state per match. Writes go through
`Match.save()` (atomic-ish: write JSON to disk, indented for hand-editing).
The web UI never edits these files behind the user's back; every mutation is
explicit (POST/PATCH/DELETE).

Filesystem-as-database trades referential safety for human operability: a
coach can copy a folder onto a thumb drive, hand-edit a misspelled name in
JSON, drop a new clip into the folder, etc.

## 5. Domain model

```
Match (backend/app/domain/match.py)
  opponent: str
  date: str
  fps: float
  clips: list[Clip]                  # ordered
  events: list[MatchEvent]           # always sorted by global frame
  opponent_roster: Roster            # away-team metadata, per-match
```

The home roster is **not** in `Match` - it's at `<Team>/roster.json` and
shared across all matches for that team.

### 5.1 Frame indexing

Frames are stored *clip-relative* on every event:

```python
@dataclass
class MatchEvent:
    clip_id: str
    local_frame: int
    state: Optional[GameState]   # computed, not persisted
```

Reordering clips, deleting a clip, or inserting a new one therefore *never*
needs to touch any event's frame number. The match resolves global frames
on demand:

```python
match.to_global_frame(clip_id, local_frame)   # local -> global
match.from_global_frame(global_frame)         # global -> (clip_id, local)
```

`ClipTransitionEvent` is the one exception: it sits at a clip boundary, not
a specific frame, and is sorted via the offset of `from_clip_id`.

### 5.2 Event class hierarchy

`MatchEvent` (base) -> dataclass subclasses for each concrete event
(`KillEvent`, `SubstitutionEvent`, `BallServedEvent`, ...). Subclasses are
registered in `domain/events/registry.py` and listed in
`domain/events/__init__.py`. Each subclass declares its impact through four
optional properties:

| Property | Says... | Examples |
|---|---|---|
| `timeline_effect` | how the rendered timeline cuts/fades around this event | cut_end (with fade_frames, frame_shift), set_end |
| `overlay_effect` | what the renderer draws on top of frames | scoreboard show/hide, player popup, message popup |
| `score_effect` | how the score changes | PointScored, ScoreDelta, ScoreReset |
| `roster_effect` | how the on-court lineup / serving team change | Substitution, ServeTeamSet, BallServed, ReplayMarker |

The base `apply()` method walks these effects and mutates `GameState`
without subclasses needing to write per-event apply logic. Subclasses
override only when they need custom semantics.

### 5.3 Game state

`GameState` (`domain/game_state.py`) is a value-typed snapshot:

```
home_score, away_score, home_sets, away_sets
serving_team
home_positions: {1..6 -> jersey | None}
away_positions: {1..6 -> jersey | None}
game_started, game_ended, in_cut_region, ball_served_since_last_score
```

Whenever `Match.events` changes, `_recompute_states()` walks all events in
order, calls `event.apply(state, all_events)` and stores the resulting
`GameState` snapshot on `event.state`. The renderer reads `event.state`
directly. The UI does too (see Section 7).

`event.state` is **not** persisted to `match.json` - it's deterministic
output of replaying events through `apply()` and is re-derived every load.

## 6. Render pipeline

`backend/app/render/`:

```
build_frame_map(match) -> FrameMap   # one entry per output frame
MatchRenderer(match, media_dir, home, away)
  .render(out_path, progress_cb)     # MoviePy VideoClip.write_videofile
```

`FrameMap` answers, for each output frame: which clip is reading, which
local frame, what overlays are visible at this moment, what color blend is
in effect (mid-fade), etc. `MatchRenderer` is then a stateless function:
"give me the pixel buffer for output frame N."

Overlays:
- `ScoreboardOverlay` reads `event.state` to know score/sets/lineup at any
  frame, and uses `TeamBranding` (name + color) for both teams.
- `MessageOverlayRenderer` draws transient text/image popups for `message`
  events.

Renders run as background jobs through `jobs/manager.py` (single-threaded
queue; only one concurrent render). Progress is streamed to the UI via
Server-Sent Events on `/api/jobs/{id}/events`.

## 7. State sync between UI and server

This is the rule: **the server is the single source of truth, and the UI
never re-derives anything the server has already computed.**

### Mutation flow

```
UI button click
  -> POST/PATCH/DELETE /api/matches/{t}/{m}/events   (fetch wrapper in api/client.ts)
  -> Match.add_event / update_event / delete_event
       -> _sort_events()
       -> _recompute_states()
       -> Match.save() (writes match.json)
  -> serialize_match(...) returns full MatchOut
  -> UI replaces local data state with the response
```

Every event mutation refetches the entire match. Cheap (events JSON is tiny)
and avoids partial-update bugs.

### State at the playhead

`EventOut.state: Optional[GameStateOut]` is serialized for every event
(except `ClipTransitionEvent`). This is the GameState **after** that event
applied. The UI looks up "live" state with one line:

```ts
// frontend/src/components/Controls/state.ts
events.findLast(e => e.global_frame !== null && e.global_frame <= currentFrame)
  ?.state
  ?? EMPTY_STATE;
```

No replay loop in the frontend. No risk of UI/render divergence on subtle
rules (`max(0, ...)` clamping in score corrections, point-scored rotation,
ball-served reset, sets accumulation, etc.).

The "state before this event" needed by the EventList summary (e.g. who was
at this position before a substitution) is just `events[i-1].state`.

## 8. API surface

All routes mounted under `/api`. Response models live in
`backend/app/api/schemas.py`.

| Path | Verbs | Purpose |
|---|---|---|
| `/api/health` | GET | unauthed health probe |
| `/api/teams` | GET, POST | list / create team |
| `/api/teams/{team}/roster` | GET, PUT | per-team roster |
| `/api/teams/{team}/matches` | GET, POST | list / create match |
| `/api/matches/{team}/{match}` | GET, PATCH, DELETE | match metadata + opponent_roster |
| `/api/matches/{team}/{match}/clips` | POST, PUT | upload / reorder clips |
| `/api/matches/{team}/{match}/clips/{id}` | DELETE | delete clip |
| `/api/matches/{team}/{match}/clips/{id}/stream` | GET | range-supporting clip stream |
| `/api/matches/{team}/{match}/events` | GET, POST | list / create event |
| `/api/matches/{team}/{match}/events/{id}` | PATCH, DELETE | edit / delete event |
| `/api/renders` | POST | start a render job |
| `/api/jobs/{id}` | GET | poll a render job |
| `/api/jobs/{id}/events` | GET (SSE) | stream render progress |
| `/api/matches/{team}/{match}/renders/{file}` | GET | download a rendered file |

Mutations to `clips` or `events` always return the full `MatchOut`.

## 9. Frontend architecture

### Pages

- `TeamSelectPage` -> `TeamDashboardPage` -> `MatchEditorPage`. Routing via
  `react-router-dom`.

### Match editor layout

Three columns on the top row, full-width timeline below:

```
+--------------------------+----------+----------+
| VideoPlayer              | Controls | Events   |
| (zoom + pan, hotkeys)    | Panel    | List     |
+--------------------------+----------+----------+
| splitter (drag to resize)                      |
+------------------------------------------------+
| Timeline (full width, scrolls horizontally)    |
+------------------------------------------------+
```

- `VideoPlayer` ([components/VideoPlayer.tsx](frontend/src/components/VideoPlayer.tsx))
  - HTML5 `<video>` wrapped in `react-zoom-pan-pinch`.
  - Treats all clips as one continuous stream (auto-advances on `ended`,
    cross-clip frame stepping, end-of-timeline rewind).
  - Robust frame-stepping (`frameFromTime`/`timeForFrame`) absorbs
    keyframe-snapping browser behavior.
- `Timeline` ([components/Timeline/Timeline.tsx](frontend/src/components/Timeline/Timeline.tsx))
  - Read-only canvas rendering: clip bars, event pins, cut regions, playhead,
    smart-spaced ticks.
  - Vertical wheel = zoom around cursor; shift/horizontal wheel = pan.
- `ControlsPanel` ([components/Controls/](frontend/src/components/Controls/))
  - Score, lineup grid, +1 buttons, action button matrix.
  - Reads `stateAtPlayhead(events, currentFrame)` for everything; never
    walks events for itself.
  - "Two-step picker": click a player-action button, then click a position.
- `EventList` ([components/EventList.tsx](frontend/src/components/EventList.tsx))
  - One row per event with a human summary built by
    [`summarizeEvent`](frontend/src/components/eventSummary.ts).

### Hotkey system

Decoupled from any specific component:

```
hotkeys/
  catalog.ts      # static ACTION_CATALOG + DEFAULT_BINDINGS
  registry.ts     # singleton: handler map, bindings (localStorage), dispatcher
  combos.ts       # KeyboardEvent.code -> canonical combo string
  useHotkeyAction # React hook that registers a handler by action id
```

Components register handlers by id (`useHotkeyAction("playback.play1x", () => playAt(1))`).
A future configurator UI mutates `bindings` only; component code never
moves. Combo strings are built from `e.code` so they're keyboard-layout
independent.

## 10. Deployment

Two modes (see README for setup):

- **Mode A (recommended): IIS reverse proxy**
  IIS terminates TLS on :443, ARR proxies `https://host/vme/*` to the
  Python service on `127.0.0.1:8000`. Frontend is built with
  `BASE_PATH=/vme/`. SSE works because ARR response buffering is
  explicitly disabled in [iis/web.config](iis/web.config).
- **Mode B: Hypercorn standalone**
  `serve.py` binds :80 (HTTP->HTTPS redirect) and :443 (TLS) directly.
  Used when IIS isn't available.

In both modes the backend is wrapped as a Windows service via NSSM,
configured in `scripts/install-service.ps1`.

## 11. Auth

`BasicAuthMiddleware` in `backend/app/auth.py`. One shared username +
bcrypt hash, both pulled from environment (`secrets.env`). The middleware
is added *before* CORS so auth runs first; OPTIONS preflights are
whitelisted. Every API path requires auth except `/api/health`.

This is intentionally simple. The tool is single-tenant; if it ever needs
real auth, swap this middleware out.

## 12. Logs

`backend/logs/backend.log` (rotated by hand). NSSM also captures stdout /
stderr to `backend/logs/service-*.log` when running as a service. Render
jobs log per-frame progress at INFO level.
