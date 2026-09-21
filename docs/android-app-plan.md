# VME Android app — build plan and execution log

Evolves the spike in [`android-streaming-spike.md`](./android-streaming-spike.md)
(steps 0 + 2 + 2b passed) into a courtside app.

## Target capabilities

1. Import a team roster from VME and preserve it locally on the phone.
2. Create a match by specifying opponent name and picking the local team.
3. **Start streaming** = spike's *3 GO LIVE*: create a titled YouTube
   broadcast, bind to the persistent stream key, capture UVC + USB audio,
   push RTMPS.
4. Annotate events in the **same format as VME** — every type in
   `frontend/src/types/api.ts::EVENT_TYPES`.
5. Overlay a live scoreboard on the video, plus transient event popups.
6. Stop scoring and streaming cleanly.

## Non-goals for v1

- No live sync to the VME backend during a match.  Match data lives on
  the phone and is exported as a JSON file that the existing importer
  (planned in the spike doc, step 1b) can ingest later.
- No local video recording — the DSLR card is the master.
- No offline sync/conflict handling — a match is one file, flushed once.

## Steps

Each step is a hand-off point: after it, the app builds and the record
below gets a "done" row.

| # | Step | Notes |
|---|---|---|
| 1 | **Data layer + home screen + roster import** | Persist teams/rosters to app files dir; a home screen lists them; an import screen fetches `GET /api/teams/{team}/roster` from a configured VME base URL. |
| 2 | **Match creation** | New-match screen picks a local team and asks for opponent name (+ date default = today). Match record persisted. |
| 3 | **Streaming wiring** | *Start streaming* in the match screen runs the spike's GO LIVE flow with the match's opponent baked into the broadcast title. |
| 4 | **Event tagging** | Buttons for every `EVENT_TYPES` entry, with a running score/serving/rotation state (ported from `backend/app/domain`). |
| 5 | **Overlay** | Scoreboard (`ImageObjectFilterRender`) + event popup animations. Redraws on state change, not per frame. |
| 6 | **Stop + export** | End streaming, write a `match.json` (VME schema) to the shareable files dir, expose via a Share intent. |

## Reused from the spike

`UvcVideoSource.kt`, `YouTubeAuth.kt`, `YouTubeLive.kt`, and the entire
UVC/RootEncoder configuration are unchanged. The existing `MainActivity`
becomes an accessible **Dev spike** screen; it stays wired end-to-end so
the spike remains reproducible.

---

## Execution log

Dates are the local calendar day the work landed on `main`.

### Step 1 — data layer + home screen + roster import — **done**

Builds clean; smoke-tested only by the compiler + a look at
`build/outputs/apk/debug/app-debug.apk`.

**What shipped**

- `data/Models.kt` — `Player`, `Roster`, `Team`. Names deliberately
  match VME's `RosterOut`/`PlayerOut` (`players`, `team_name`,
  `team_color`, `short_name`) so a match record produced by this app
  can be imported by the editor without translation.
- `data/Storage.kt` — `TeamStore`, backed by `filesDir/teams/<slug>/roster.json`.
  Writes go through a temp file + rename so a killed process cannot
  leave half-written JSON on disk. Bookkeeping fields (`_source_url`,
  `_imported_at`) are underscored so the file remains visibly
  RosterOut-shaped to anyone reading it. Includes a Kotlin `slugify()`
  so an in-app team can be created without asking the server for a slug.
- `data/VmeClient.kt` — synchronous `HttpURLConnection` calls to
  `GET /api/teams` and `GET /api/teams/{team}/roster`. No OkHttp: two
  GETs do not justify the dependency. 10-second timeouts on both
  connect and read so a wrong URL fails visibly instead of hanging.
- `ui/HomeActivity.kt` — new launcher. Lists imported teams, exposes
  **Import roster**, **New match** (stub until step 2), and **Dev
  spike** (kept reachable because the harness is still the only
  end-to-end capture test — a regression here must not force a
  `git checkout` to diagnose).
- `ui/RosterImportActivity.kt` — base URL + list + tap-to-import.
  Base URL persists in `SharedPreferences` under `vme.base_url` so the
  second import is one tap. Whole screen is a log because every
  failure mode (wrong URL, wrong network, backend down) wants a
  specific error line more than a nice UI.
- `res/xml/network_security_config.xml` — global cleartext allowance.
  Scoping to RFC 1918 does not work: `<domain>` is a literal host,
  not a CIDR, so `192.168.1.0` would only match that exact address.
  RTMPS to YouTube is unaffected.
- `AndroidManifest.xml` — `HomeActivity` becomes `LAUNCHER`;
  `MainActivity` (the spike) stays exported so the Dev spike button
  and any old shortcuts still work.

**What is deliberately not done**

- No Compose. RootEncoder pins Kotlin 2.3.21 (its `.kotlin_module`
  metadata) and matching a Compose compiler to that would restart the
  version fight the spike README warns against. Plain views are
  cheaper here than the fight.
- No coroutines / lifecycle-aware scopes. Two synchronous HTTPs on a
  bare `Thread { }` and `runOnUiThread` back is 12 lines; adding
  coroutines would be 12 lines *plus* a dependency.
- No team logos yet. VME serves them from a separate endpoint
  (`/logos/...`) and the overlay does not need them until step 5.
- No refresh button on the home screen list. `onResume` reloads from
  disk, which covers the only path in — returning from the import
  screen.

**Files added**

```
android/app/src/main/java/works/vme/streamer/data/Models.kt
android/app/src/main/java/works/vme/streamer/data/Storage.kt
android/app/src/main/java/works/vme/streamer/data/VmeClient.kt
android/app/src/main/java/works/vme/streamer/ui/HomeActivity.kt
android/app/src/main/java/works/vme/streamer/ui/RosterImportActivity.kt
android/app/src/main/res/xml/network_security_config.xml
```

**Files changed**

```
android/app/src/main/AndroidManifest.xml   # launcher swap + new activities + net config
```

**Next**: step 2, match creation. The store already knows about
teams; a `Match` model + a picker screen + a matches directory under
`filesDir/matches/` is the shape.

### Step 2 -- data layer for matches + match creation -- **done**

**What shipped**

- `data/Match.kt` -- `Match`, `MatchEvent`, `GameState`, `EventType`,
  `Side`. Every `EventType.wire` string is a value from
  `frontend/src/types/api.ts::EVENT_TYPES`, exact-match, so a
  match.json written here is import-ready without translation.
  `payload` on `MatchEvent` is `Map<String, Any?>` rather than a
  sealed hierarchy: every VME event carries a different payload, and
  enumerating them as Kotlin subclasses would be more code than
  either producer (this file and the tagging UI) benefits from.
  Phone-only fields (`_at`, `_youtube_video_url`,
  `_stream_started_at`) are underscored so the export stays visibly
  VME-shaped.
- `data/MatchStore.kt` -- atomic file-per-match under
  `filesDir/matches/<slug>/match.json`. `createMatch()` produces a
  sortable slug (`yyyyMMdd_NN_Opponent`) and a per-day `match_index`
  so two matches on the same day against the same opponent do not
  collide.
- `ui/MatchSetupActivity.kt` -- home-team picker + opponent + date +
  tournament folder. Date defaults to today; tournament defaults to
  `"Streamed"` so a courtside-created match imports into a
  discoverable subfolder in VME rather than the tournament root.
  Persists the match and drops straight into `MatchLiveActivity`;
  the button is called "Create + open" because the two actions
  reliably happen together.
- `HomeActivity` -- now lists matches (newest first) alongside teams,
  with per-match **Open** and **X** actions. The **New match** button
  is disabled while no team exists, so the missing dependency is
  visible rather than a crash on the setup screen.

**Gotcha found and fixed**

- **Kotlin block comments nest.** A `/*` inside a `/**` KDoc opens a
  nested comment that needs its own `*/`; the outer `*/` closes only
  the inner one. Writing `backend/app/domain/events/*` in a KDoc
  broke compilation of `Match.kt` and `GameStateEngine.kt` with a
  "Unclosed comment" at EOF, which then cascaded through every file
  that referenced `Match` or `EventType` and produced ~40 unrelated
  errors. Fix: rewrite the phrase without a trailing `/*`. Worth
  writing down: the error location is the file with the unclosed
  comment, not the ones that fail to see the resulting missing
  symbols.

### Step 3 -- streaming wired to a match -- **done (compiler only)**

On-phone verification still owed: this reuses the exact
`UvcVideoSource` + `GenericStream` + `YouTubeAuth` + `YouTubeLive`
code that step 2 of the spike already ran on hardware, so the risk is
in the plumbing, not the primitives.

**What shipped**

- `ui/MatchLiveActivity.kt` -- one screen, two columns: preview +
  status + log + controls on the left, event-tag grid on the right.
  Landscape (`screenOrientation="landscape"` in the manifest) because
  a portrait phone cannot fit both panels usably.
- **Start streaming** does everything the spike's `1 PREVIEW` +
  `2 STREAM` + `3 GO LIVE` did, in order, off one press: prepare,
  audio routing, filter attach, preview, then `YouTubeAuth` ->
  `listStreams` -> `createBroadcast` -> `bind` -> `startStream`.
  The three-button spike existed to isolate failures; a match-day
  operator wants one button.
- Broadcast title is `"{home} vs {opponent} -- {yyyy-MM-dd}"`, built
  from the match record, delivering on the spike's step 2b: no more
  column of identically-named auto-created broadcasts.
- `broadcast.watchUrl` and `System.currentTimeMillis()` are persisted
  on the match as `_youtube_video_url` and `_stream_started_at` so
  they survive a restart and end up in the export.

**Inherited from the spike, not fixed here**

- The **open bug** at the bottom of `docs/android-streaming-spike.md`
  ("the phone's bytes never reach the bound stream") is still open.
  This app hits the same code path; a match-day test will hit the
  same failure. The two hypotheses the spike wrote down (explicit
  `:443` port, stale bound broadcast) are still the first things to
  try, and this app does not yet use `rtmpsIngestionAddress` from
  the API response -- it uses `YouTubeLive.INGEST_PRIMARY` like the
  spike does. Fixing that is a one-line change in `goLiveWithToken`
  once the bug is isolated on hardware; keeping the app parallel to
  the spike here is deliberate so the fix lands in exactly one
  place.

### Step 4 -- event tagging -- **done**

**What shipped**

- `logic/GameStateEngine.kt` -- stateless fold over the event list.
  Ports the subset of `backend/app/domain/events/` that a courtside
  operator can produce: scoring (`Score`/`Kill`/`Ace` rotate the
  winning side if not already serving, then transfer serve),
  `ScoreCorrection` deltas, `SetEnd` (winner takes the set, scores
  and positions reset), `GameStart`/`GameEnd`/`FirstServe`/
  `BallServed`/`Replay`, home-team substitutions. Stat-only events
  (`Assist`, `Block`, `Dig`, `Dive`, `Highlight`, `Focus*`, `Cut*`,
  `ClipTransition`, `Message`) log through unchanged: they belong on
  the timeline but do not move the scoreboard.
- **No validation.** VME's engine also reports warnings/errors; here
  they are deliberately dropped because blocking a tap at the gym
  for a suspected out-of-order serve is worse than letting the
  operator fix it in the editor later.
- **Every `EVENT_TYPES` entry has a button** in `MatchLiveActivity`,
  grouped Lifecycle / Serve / Score / Player actions / Marks. The
  two big coloured buttons (**+1 US** blue / **+1 THEM** red) are
  the courtside path -- one thumb, no dialogs.
- Player-scoped taps (`Kill`, `Ace`, `Assist`, `Block`, `Dive`,
  `Dig`, `Highlight`, `Substitution`) go through a two-step dialog:
  team -> jersey, with a **"+ Add jersey"** row that appends a
  placeholder `Player(name="#N", number=N)` to the roster on the
  fly. Opponent rosters need this almost always; imported home
  rosters occasionally.
- Every tap `store.save(match)`s **before** returning to the UI. A
  killed process loses at most the tap in flight -- which is why the
  store's writes go through temp-file + `renameTo`.

### Step 5 -- overlay -- **done (compiler only)**

**What shipped**

- `overlay/ScoreboardOverlay.kt` -- Canvas-into-ARGB_8888 Bitmap,
  handed to `ImageObjectFilterRender.setImage()`. The bitmap is the
  full video resolution because `ImageObjectFilterRender` treats
  bitmap pixels as encoded pixels; leaving `setScale` at its default
  30x22 would silently make the overlay tiny. Explicit
  `setScale(100, 100)` + `setPosition(CENTER)` documents intent.
- **Redraws only on state change**, not per frame -- exactly what the
  spike doc's step 3 requires. Nothing runs per-frame on the GPU
  beyond a textured quad sampling the last bitmap.
- Two panels (home top, away below), team-coloured accent stripe,
  serving indicator dot, set pips, big score readout right-aligned
  in a darker sub-panel. Loosely mirrors
  `backend/app/render/overlays/scoreboard.py`; a pixel-for-pixel
  port is a polish task, not a v1 requirement.
- Event popups are a second draw inside the same bitmap (top-right).
  Timed with `popupUntil`; hidden on the next state-change redraw
  after expiry. There is **no per-frame ticker** -- if the operator
  taps nothing for a minute the popup stays visible for a minute.
  Given that scoring taps happen every ~30 s of live play, this is
  fine; a proper fade-out is a step-7 polish item.

**Gotcha found and fixed**

- **`object` is a Kotlin keyword** and appears in the fully-qualified
  package name
  `com.pedro.encoder.input.gl.render.filters.object.ImageObjectFilterRender`.
  Escape with backticks:
  `` `object`.ImageObjectFilterRender ``. Left an inline comment at
  the import so the next reader does not spend an evening on it.

### Step 6 -- stop + export -- **done**

**What shipped**

- **Stop** in `MatchLiveActivity` tears down stream + preview, clears
  the overlay filter reference, resets the button state, and offers
  a share sheet on the same modal. Immediate offer because the
  operator is most likely to want it here, one tap after the match.
- **Export** builds a `content://` URI via `FileProvider` and fires
  `ACTION_SEND` with `application/json` -- Drive, Files, mail, etc.
  A raw `file://` URI would be rejected on API 24+ (StrictMode's
  `FileUriExposedException`); FileProvider is the sanctioned path.
- `res/xml/file_provider_paths.xml` scopes the authority to
  `filesDir/matches/` only, not the whole `filesDir`, so a future
  misuse cannot accidentally share e.g. a cached OAuth blob under
  the same authority.
- `AndroidManifest.xml` declares the `FileProvider` at
  `${applicationId}.fileprovider` -- same authority string used by
  `FileProvider.getUriForFile()`.

**What is deliberately not done**

- No live sync to the VME backend. The export **is** the sync,
  matching the plan-doc scope decision. `POST
  .../matches/{match}/import` is the VME side of this pair and
  belongs on the backend, not the phone.
- No fade-out on event popups. Redraws are state-change-driven; a
  per-frame ticker would break the "redraw only when state changes"
  guarantee this file was built on.
- No opponent rotation tracking. VME does not track it either --
  the operator sees only their own team's court.
- No foreground service around streaming. The spike documents this
  as a step-2 improvement; on Android 15/16 a screen-off during a
  match would kill the stream. Worth doing before match day, not
  before the first hardware run.

---

## Verification owed on hardware

The build compiles and lays out sensibly. What has **not** been
exercised on the phone:

1. **Overlay filter attachment order.**
   `stream.getGlInterface().addFilter(ov.filter)` is called after
   `prepareVideo` and before `startPreview`. That is what RootEncoder
   examples do; verify no black frames or filter-not-applied logs
   when a real capture starts.
2. **The overlay bitmap at 1920x1080** is ~8 MB in RAM, allocated
   once. Watch for a Bitmap-allocation OOM on a low-mem phone
   (the S24 has plenty).
3. **`ImageObjectFilterRender.setImage()` from the UI thread** while
   the encoder is running. RootEncoder's own examples call it that
   way; if it turns out to require the GL thread there is a hook for
   posting onto it.
4. **The open RTMP bug** from the spike is expected to reappear
   verbatim. See step 3 above and the spike doc's OPEN BUG section.
5. **`YouTubeLive.INGEST_PRIMARY` vs. `rtmpsIngestionAddress`.**
   Once the open bug is understood, the one-line fix (use the address
   the API returns instead of the constant) belongs in
   `YouTubeLive.kt` so both the spike and this app get it.

Under-tested does not mean unwritten. It means the first match-day
rehearsal is the acceptance test for these six steps, and this
document is the checklist that rehearsal reads from.

---

## Files added / changed across all six steps

```
data/Models.kt                                          (step 1, new)
data/Storage.kt                                         (step 1, new)
data/VmeClient.kt                                       (step 1, new)
data/Match.kt                                           (step 2, new)
data/MatchStore.kt                                      (step 2, new)
logic/GameStateEngine.kt                                (step 4, new)
overlay/ScoreboardOverlay.kt                            (step 5, new)
ui/HomeActivity.kt                                      (step 1 new, step 2 updated)
ui/RosterImportActivity.kt                              (step 1, new)
ui/MatchSetupActivity.kt                                (step 2, new)
ui/MatchLiveActivity.kt                                 (steps 3-6, new)
res/xml/network_security_config.xml                     (step 1, new)
res/xml/file_provider_paths.xml                         (step 6, new)
AndroidManifest.xml                                     (steps 1, 2, 6 updated)
```

All paths under `android/app/src/main/` and, for the Kotlin sources,
under `java/works/vme/streamer/`.

Reused unchanged from the spike:
`MainActivity.kt`, `UvcVideoSource.kt`, `YouTubeAuth.kt`,
`YouTubeLive.kt`, and every RootEncoder / UVCAndroid configuration
choice documented in the spike README.

---

## Post-v1 fixes

Found on the first on-device run.

### Import failed with HTTP 401 -- **fixed**

The backend at $HOME sits behind IIS Windows Authentication.
Android's stock `HttpURLConnection` does not speak Negotiate/NTLM
(those are OS-integrated on desktop JVMs and no-ops here), so the
unauthenticated GET came back as 401 and the log line was silent
about why.

**What shipped**

- `data/VmeClient.kt` grew a `Credentials(user, password)` type and
  optional `creds` parameter on both `listTeams` and `fetchRoster`.
  When present, we send `Authorization: Basic base64(user:pass)`.
  Basic is the only WWW-Authenticate scheme we can produce without
  pulling in `jcifs-ng` (NTLM) or a Kerberos stack -- both are
  significant dependencies for what a single IIS checkbox
  ("Basic Authentication") replaces. `DOMAIN\user` and `user@domain`
  both work as the user string; IIS accepts either.
- 401 responses now log the `WWW-Authenticate` challenge header,
  which tells the operator at a glance whether the server is asking
  for Basic (fixable by typing credentials) or Negotiate/NTLM
  (needs a backend config change).
- `ui/RosterImportActivity.kt` grew user + password fields.
  Persisted alongside the base URL in `SharedPreferences` under
  `vme.user` / `vme.pass`. Not encrypted: this is a single-purpose
  courtside device on a LAN, and encrypting the prefs without a
  key-management story only pretends to add security. Both fields
  blank -> no `Authorization` header sent (right thing for a dev
  backend without auth).

**Backend-side follow-up not owned by this repo**

Enable **Basic Authentication** on the IIS site alongside Windows
Authentication. Without that, no credential this app can produce
will be accepted.

### Password field not masked -- **fixed**

`InputType.TYPE_TEXT_VARIATION_PASSWORD` alone is not enough on
every Android keyboard/IME combination -- some fill without masking.
The reliable pair is `inputType | PasswordTransformationMethod`.
Both applied to `passInput` in `RosterImportActivity`, plus a
monospace typeface so `"*" * n` renders at consistent width.

### Match screen redesigned to match the VME editor -- **fixed**

The first cut was a landscape two-column layout with the video
preview always visible and every EVENT_TYPES button rendered as a
cell in a grid. Two problems on the first on-device run:

1. The preview ate ~half the screen even though the operator is
   watching the actual game, not the phone.
2. The button grid did not match how a courtside operator thinks:
   scoring for our team, scoring for them, and a rotation to keep
   track of are the three high-frequency operations; everything else
   is a match-lifecycle tap that happens once per game or set.

**What shipped**

- `AndroidManifest.xml`: `MatchLiveActivity` is now `portrait`.
- `ui/MatchLiveActivity.kt`: rewritten around the VME editor
  courtside screenshot. Layout:

  ```
  header: [Events] [Preview]  status   [Start] [Stop] [Export]
  ---------------------------------------------------------------
  Events pane:
    big score readout (team names in accent colour)
    HOME card (coloured border)
      row: name + serving dot + [Sub] + [-1] + [+1]
      6-slot rotation grid (P4 P3 P2 / P5 P6 P1)
      player tags: Kill/Ace/Dig/Dive/Block/Highlight
    AWAY card (compact)
      row: name + serving dot + [-1] + [+1]
    MATCH section:
      Ball Served / Replay / Cut Start / Cut End /
      Game Start / Game End / End Set /
      First serve H / First serve A
  Preview pane:
    SurfaceView + streaming log
  ```

- **Removed events** (per the reference layout): `Focus In`,
  `Focus Out`, `Message`, the Score Correction dialog, `Assist`,
  and `Clip Transition`. `Focus`/`Message`/`Assist` were never used
  courtside in VME; `ClipTransition` is post-edit; Score Correction
  is replaced by the `-1` buttons.
- **`-1` / `+1` on team headers**: `+1` writes a `Score` event as
  before; `-1` writes a `ScoreCorrection` with `home_delta` or
  `away_delta` = -1. VME's engine already knows how to fold those.
- **Confirmation prompts** (both are `AlertDialog`s):
  1. `+1` when `gameState.ballServedSinceLastScore == false`.
     Catches mis-taps where the operator adds a point before the
     rally actually happened (state engine already tracks this
     flag, we just consume it here).
  2. `-1` on either side, always. A subtraction is either a
     correction or a mis-tap; the cost of one confirm tap is
     cheaper than the cost of a mis-recorded correction.
- **Rotation grid**: 3x2 `Button`s (P4/P3/P2 top, P5/P6/P1 bottom,
  the way the operator sees the court from behind). Each cell
  shows `P{n}\n?` empty or `P{n}\n#{jersey}` when a jersey is
  assigned. Tapping a cell opens the player picker and records a
  `Substitution` for that position, skipping the position-picker
  step of the Sub dialog.
- **Tab switching**: `[Events]` / `[Preview]` toggle at the top of
  the header bar; a `FrameLayout` holds both panes and swaps
  `View.VISIBLE` / `View.GONE`. Kept as one activity (not two,
  not `ViewPager2`) because the `SurfaceView` must not be
  recreated on every switch or `startPreview` has to re-run;
  visibility toggle preserves the surface.
- **Streaming controls in the header bar** (Start/Stop/Export) so
  the operator can stop the stream from either tab without hunting
  for it.

**Kotlin-tooling gotcha (not code)**

The `edit` tool is transactional per call: any failing `edits[i]`
rolls back all sibling edits. Landing a many-hundred-line UI rewrite
as one huge `oldText` block was fragile because a single subtle
whitespace mismatch reverted everything. Second attempt used `write`
for the whole file, which is what a rewrite that big deserves.

### VME round 21 -- **OneDrive, and settings that admit there are two engines**

#### 1. `app/upload/onedrive.py`

Graph upload sessions rather than a simple PUT: simple uploads cap at
250 MB and a match render is well past it. Chunks are aligned to the
320 KiB multiple Graph requires, 10 MiB by default. `upload_file` takes
the **same** keyword signature as `youtube.upload_video` -- same
`progress_cb`, same `cancel_check` -- so the render job can route to
either without special-casing either one.

Not `msal`: it would be a new dependency for two HTTP calls `requests`
already covers, and its token cache is its own format on disk. Matching
`youtube.py`'s shape is worth more here than matching Microsoft's
samples.

Two things return None rather than raising, because both are
configuration facts and neither is a reason to throw away a file that
uploaded perfectly well:

- `create_share_link` -- Business and SharePoint tenants routinely
  forbid anonymous links by policy.
- `set_thumbnail` -- Graph documents custom thumbnails as **OneDrive
  Personal only**. The durable answer on any drive is the title card
  `render/thumbnail.py` already draws.

#### 2. Microsoft consent, alongside Google

`/auth/microsoft/{status,client,start,callback,disconnect}` mirroring
the Google set, sharing the same pending-state machinery. Microsoft
takes a client id and secret as two fields rather than a downloaded
JSON, and `common` as the tenant so personal and work accounts can both
consent.

**A real bug the tests caught.** `return_to` was validated with
`startswith("/")`, which `//evil.example/x` satisfies -- and every
browser reads a protocol-relative URL as `https://evil.example/x`. That
made the callback an open redirect, on **both** providers.
`_safe_return_to` now rejects `//` and `/\` as well. Verified against
four inputs on each provider.

#### 3. Which engine, per team

`UploadConfig` on the roster: `match_destination`, `reel_destination`,
plus the OneDrive folder template and whether to make share links.
Defaults keep every existing team on YouTube for both, so adding it
changes nothing until someone chooses.

Read at **upload time**, not baked into the job at enqueue: a queue can
sit for days waiting out a quota reset, and the answer that matters is
the one in force when the bytes move. An unknown engine degrades to
YouTube rather than failing the load, so a roster written by a newer
build cannot make a team unopenable on an older one.

`_run_onedrive_upload` is a separate path rather than a branch inside
the YouTube one, because almost none of that function applies: no quota
ledger, no playlist, no privacy status, no second call for a thumbnail.
Folder names are sanitised -- Graph rejects `" * : < > ? / \ |`, and an
opponent typed with a slash is not hypothetical.

#### 4. Settings became a page with subpages

One scrolling stack was fine while every upload was a YouTube upload.
With two engines it asks the reader to work out which half applies:
a playlist and a privacy status are YouTube's vocabulary, a folder path
and a share link are OneDrive's.

`/teams/:team/settings/:section` -- Connections, Uploads, YouTube,
OneDrive, File naming, Media server, Roster. The section is in the URL
so a link can point at one, and so the OAuth callback has somewhere
specific to return to. The dashboard keeps tournaments, the render
queue and the renders list, and gains a link.

The engine choice is rendered as two cards rather than a `<select>`,
because the trade-off between them *is* the decision and a dropdown
hides it.

#### 5. `redirect_uri` mismatch -- the callback was being built as http

The first real consent attempt failed at Microsoft with
`invalid_request: The provided value for the input parameter
'redirect_uri' is not valid`.

Reproduced by rebuilding the production scope: `--root-path /vme` is
set and the `/vme` prefix came out correctly, but the **scheme** was
`http`. IIS's ARR does not forward `X-Forwarded-Proto` unless the
server variable is whitelisted in `applicationHost.config`, so uvicorn
sees plain http even though the request arrived over TLS.

The panel therefore displayed -- and `start` sent --
`http://satis2.duckdns.org/vme/...`. No provider accepts a plain-http
callback for a public host, so the registered URI could only have been
the `https://` one, and the two never matched.

Fixed in the app rather than by asking for an IIS change:
`_callback_uri` forces https for any host that is not loopback. That
cannot be wrong in a way that matters -- an http callback to a public
host is unusable by definition -- and loopback keeps http so local
development still works.

`VME_PUBLIC_URL` overrides it outright for deployments whose host
header is not what the world uses. **Origin only**: the path always
comes from `url_for`, which already carries the `/vme` prefix, so
pasting a full URL would otherwise prefix it twice. A bare host is
accepted and assumed https, because that is how people write one.

#### 6. ...and then the return trip lost the `/vme` prefix

Consent then succeeded -- `microsoft_auth=ok`, token saved -- but the
browser landed on `/teams/Eastlake_JV/settings?microsoft_auth=ok` and
IIS answered 404. The real page is at `/vme/teams/...`.

`BrowserRouter` is mounted with `basename="/vme"`, so every React
Router path omits the prefix. The settings page was handing one of
those to the server as `return_to`, and the server redirected to it
literally.

Fixed by **removing the prop**. `UploadConnections` now derives its own
return path from `window.location.pathname`, which is the only value
correct in both dev and production. A caller cannot pass the wrong
thing because there is nothing left to pass -- the component always
means "come back to where I am", and that was never worth
parameterising.

Two prefix bugs in a row, opposite directions: the API callback needed
the prefix *added* by `--root-path`, and the app path needed it *not
stripped* by the router. Worth remembering that the two live in
different coordinate systems.

#### 7. The renders page knew only about YouTube

Three things, all the same root cause -- the page was written when
there was one engine.

**The enqueue endpoint refused unless *YouTube* was configured.** That
made a OneDrive-only setup impossible to use: nothing could be queued
even though nothing was going to YouTube. It now resolves the
destination first and checks the provider that file is actually going
to.

**The queue was already right.** `_run_upload` resolves the
destination before `quota.can_afford`, so a mixed queue drains the
OneDrive half while the YouTube half waits out the reset. The *button*
implied otherwise by being disabled on YouTube status, which is now
skipped for OneDrive-bound rows. The quota strip is labelled as
YouTube's rather than reading as a limit on everything.

**Destination is recorded per file.** `_write_onedrive_sidecar` already
wrote `<file>.onedrive.json`; the scanner now reads it into
`FullRenderInfo` alongside the YouTube one, and adds
`upload_destination` -- where a *future* upload would go, from the
team's config and the file's kind.

Those are deliberately two different questions. `upload_destination`
drives the button label, so the operator is told what the button will
do. Where it *already went* comes from the sidecar, because the
setting can change after the fact and the row must keep telling the
truth about the copy that exists.

A row now reads "▶ on YouTube" or "▶ in OneDrive", linking to the
video or the share link. A OneDrive upload whose tenant forbade even a
signed-in link says "in OneDrive (no link)" -- it is up, there is just
nowhere to point. The thumbnail-replace button is hidden on OneDrive
rows: `thumbnails.set` is a YouTube call with no equivalent worth
offering.

One quieter bug fixed with it: bulk-select used `!youtube_video_id` to
mean "not uploaded", so it offered to upload files already in
OneDrive. Both engines write a sidecar, so there is now one
`isUploaded` predicate and "already uploaded" cannot mean two things.

#### 8. The first OneDrive upload worked, and then sat at 100% RUNNING

The file went up, the sidecar was written, the bar reached 100% and
said "Uploaded to OneDrive" -- and the job never left RUNNING.

`_run_onedrive_upload` finished with `JOBS.update(phase="done", ...)`.
The YouTube path ends with `JOBS.mark_done(...)`. Those look
interchangeable in the UI, because both produce the word "done", and
they are not remotely the same thing: **phase is a label, status is
the state machine.** Only `mark_done` sets `status=DONE`, stamps
`finished_at`, and lets the dispatcher move on.

Fixed by calling `mark_done`, with the message set just before it
because `mark_done` does not carry one.

Worth noting for anything else that grows a second implementation of
an existing job type: the completion contract is `JOBS.mark_done`, and
copying the *shape* of a running job's updates is not enough to end
one.

#### 9. OneDrive was showing its own frame-grab, not our card

`onedrive.set_thumbnail` was written and **never called** -- the
function existed, nothing invoked it, so OneDrive fell back to
sampling a frame of gym floor.

Now wired, in the same order the YouTube path uses: generate the
thumbnail *before* the transfer (seeking a frame is the slow part and
is better spent before the upload than between the upload finishing
and the thumbnail call), then set it after, once there is an item id.

Best effort, and it genuinely will not work everywhere: Graph
documents custom thumbnails as OneDrive **Personal** only, and a
Business or SharePoint drive refuses them. The sidecar records
`thumbnail_set` so a refusal reads as "OneDrive picked this one"
rather than as something broken.

**The durable answer is still the title card.** A 1-2 second card
burnt onto the front of the render works on every host and every drive
type, survives moving between them, and is better for the viewer
anyway. `render/thumbnail.py` already draws exactly that image.

#### 10. "Forget this upload" only forgot YouTube

It deleted `<file>.youtube.json`. A OneDrive row has no such file, so
the call reported success and changed nothing -- the row kept saying
"in OneDrive" because `<file>.onedrive.json` was still there.

The endpoint now reads both records. OneDrive-only clears without the
existence check: YouTube gets one because a stray click could orphan a
real video and `videos.list` costs a single quota unit, whereas a
OneDrive file sits in a folder the operator can see for themselves,
and fetching the item to ask would buy nothing.

When **both** records exist -- possible after the destination changed
between uploads -- both are cleared, because the button means "this
render is not uploaded anywhere".

#### 11. The thumbnail was never being generated at all

The OneDrive files kept their frame-grab. Not the wiring -- the log
showed `generate_thumbnail` failing outright:

```
WARNING app.render.thumbnail: could not write image for ...reel.mp4
  File "thumbnail.py", line 306, in _draw_logos
```

`_logo_disc` returns **None** for a missing logo path, and
`_draw_logos` composited the result unguarded. An opponent with no
uploaded crest -- the normal case -- raised an AttributeError that took
the entire thumbnail down. No file on disk, so the `is_file()` guard
skipped `set_thumbnail`, and the host used a frame of empty court.

**This is a pre-existing VME bug, not one this round introduced.** It
breaks YouTube reel thumbnails identically, and has been doing so for
every match without an opponent logo. Wiring OneDrive only made it
visible.

Every disc now has a fallback and a side with nothing left to draw is
skipped instead of composited as None:

- home: player photo -> jersey badge -> team initials
- away: crest -> team initials

`_initials` mirrors the phone's `StreamThumbnail.abbreviate` --
"Bellevue Storm" -> BS, "Woodinville" -> WOO, digits kept so
age-group sides stay distinguishable.

Verified by rendering the failing case and **looking at it**: the card
comes out with `#8` on the left, `WOO` on the right, VS plate, names,
caption and subject banner. Also checked no-logos-no-badge and
no-names-at-all, which previously crashed and now render.

#### 12. Connections moved to server settings

They were on the team settings page, next to the rest of the upload
configuration, carrying a paragraph explaining that they were not
actually per-team. That note was the tell: one Google token authorises
one channel and one Microsoft token one drive, **for everything this
server does**. A component that has to explain why it is where it is
belongs somewhere else.

Now at `/settings` — "Server settings", linked from the header beside
Teams. The paragraph is gone, because the placement no longer needs
defending.

Structured with sections from the start despite having one, so the
next server-wide thing (storage paths, queue limits, auth) has an
obvious home rather than being wedged into a team. The tab strip stays
hidden while there is only one.

Team settings keeps what genuinely differs per team — which engine,
which playlist, which folder — and its default tab is now Uploads. The
OneDrive panel links across to where the account is actually
connected.

The OAuth return path needed no change: `returnHere()` reads
`window.location.pathname`, so consent started from `/vme/settings`
comes back to `/vme/settings`.

#### 13. 422 uploading the client-secret JSON

`fetchJson` set `Content-Type: application/json` on every request. A
`FormData` body needs the browser's **own** content type, because that
is what carries the multipart boundary; overriding it left FastAPI
unable to find any parts:

```
422 [{"type":"missing","loc":["body","file"],"msg":"Field required"}]
```

which reads like the endpoint rejecting the file rather than a header
problem. The client code even carried a comment saying "no
Content-Type header: the browser must set the multipart boundary
itself" -- describing an intention the code did not implement. The
existing logo uploads work only because they bypass `fetchJson` and
call `fetch` directly.

Fixed in the helper rather than by copying that bypass, so the next
multipart call does not meet the same wall. Confirmed both directions:
multipart -> 200 with `kind: "web"`, a JSON content-type -> the exact
422 above.

A second, latent bug went with it. The helper merged headers and then
spread `...init` **after** them:

```ts
{ headers: { "Content-Type": ..., ...init?.headers }, ...init }
```

so any caller passing a header of its own replaced the merged object
wholesale and silently lost the content type. `...init` now comes
first. Nothing was hitting it yet -- the three callers that set headers
all use `fetch` directly -- but it was a trap sitting in shared code.

#### The shape of this round's bugs

Four in a row, all the same shape: a second engine added beside an
assumption that there was only ever one.

- `_run_onedrive_upload` set `phase="done"` where the contract is
  `JOBS.mark_done`.
- `onedrive.set_thumbnail` was written and never called.
- The enqueue endpoint refused unless *YouTube* was configured.
- "Forget this upload" deleted only the YouTube sidecar.

None were subtle once looked at, and none were caught by types or
lint, because each is a *missing* call rather than a wrong one. The
pattern worth remembering: when a code path grows a second
implementation, the risk is not the new path being wrong -- it is
every *other* place that still names the old one directly.

#### Separate queues per engine?

Considered and not done. The dispatcher already skips a deferred job
and picks the next runnable one -- `defer_until` in the future is a
skip, not a head-of-line block -- so a YouTube upload parked for a
quota reset does **not** hold up a OneDrive upload behind it. Separate
lanes would add a scheduling concept to solve a problem the existing
one already handles.

The stuck job looked like queue blocking and was not; it was one job
failing to terminate.

#### Verified

Per-render destination and OneDrive state resolve correctly against a
team configured with matches on YouTube and reels on OneDrive.

Callback generation across six shapes: production behind IIS, an
already-https request, loopback, both providers, and three forms of
`VME_PUBLIC_URL` including one with a path.

Upload config round-trips through `PUT /roster`, survives a client that
omits it, and degrades on an unknown engine. Destination routing
resolves per team and per file kind, falling back to YouTube for a
missing roster. Path sanitising handles every character Graph rejects.
Both auth providers refuse open redirects, unknown state, and
unconfigured starts. `tsc -b` and the gated backend lint are clean.

**Not verified: any actual OneDrive traffic.** No Azure app is
registered yet, so the upload session, share links and thumbnails have
never run against Graph. The first real upload is the acceptance test.

### VME round 20 -- **OAuth in the browser**

Authorising the server meant running `scripts/yt-authorize.ps1` *on the
box*. The thing that reliably breaks uploads is an expired refresh
token, so the one failure most likely to need fixing was the one that
required a shell on the server -- which usually meant the person who
noticed could not be the person who fixed it.

`app/api/auth.py` moves it to a button: status, client-secret upload,
consent, disconnect. The script still works and remains the fallback
for a server with no public URL.

#### What this needs that the script did not

The script uses a **Desktop** OAuth client, which redirects to
`http://localhost:<port>` and works only because the browser and the
script share a machine. A flow driven from another device cannot use
that, so it needs a **Web application** client with

    <public VME url>/api/auth/google/callback

registered. A Desktop client is *detected* and explained in the panel
rather than being left to fail at Google with a
`redirect_uri_mismatch` nobody can act on.

The callback URL is built from the request rather than configured, so
moving host does not need a settings change -- which also means it is
only right because IIS forwards `X-Forwarded-Proto` / `-Host`.

#### Details that matter

- `access_type=offline` **and** `prompt=consent`. Without the second,
  Google returns only an access token when the scopes were already
  approved, and the refresh token is the entire point. A response with
  no refresh token is rejected rather than saved, because uploads
  would then die in an hour with no way back.
- `state` is a single-use in-memory nonce with a 10-minute sweep. A
  restart mid-flow invalidates it, which is correct -- the alternative
  is persisting a CSRF token, and the cost of losing one is pressing
  Connect again.
- `return_to` must be a relative path. An absolute one would make this
  an open redirect.
- The panel is **server-wide** and says so. It sits on the team page
  because that is where the upload settings are, but one token
  authorises one channel for the whole server, and the placement would
  otherwise imply per-team accounts.
- Disconnect removes the token and keeps the client secret: signing in
  as someone else should not mean re-uploading the client JSON.

#### Verified

Against a throwaway data dir: no client -> explained and `start`
refused; a **Desktop** client -> detected, `start` refused with the
reason; a **Web** client -> auth URL carries `access_type=offline`,
`prompt=consent`, `state` and `redirect_uri`; `return_to` of
`https://evil.example/x` -> redirected to `/`, not off-site; a
replayed or unknown `state` -> `expired`; a non-JSON upload -> 400.

**Not verified:** no real consent round-trip, which needs the Web
client registered in Cloud Console first.

#### Still to come

The **upload-engine picker** the same request asked for is not here.
It would currently offer one engine, and a picker with one option is
noise -- it lands with the OneDrive uploader, which is the thing that
makes the choice real.

### Courtside iteration round 19 -- **team settings, announcements**

#### 1. Tapping a team opens its settings

`ui/TeamSettingsActivity`, reached from the home screen the same way a
match row opens a match. Four fields:

- **Stream title** and **Description** templates, with `{team}`,
  `{opponent}`, `{date}` and `{tournament}`. A live preview renders
  them against a stand-in fixture, because a template is hard to check
  in the abstract and impossible to check at courtside speed.
- **Playlist ID** -- every broadcast is filed there on creation.
- **Announce to** -- who gets emailed when the stream starts.

Kept separate from `SettingsActivity`, which holds the one server this
phone talks to: that is a property of the device, these are properties
of a club, and a phone covering two teams needs two sets.

The naming fields deliberately mirror VME's per-team `youtube` block
(`title_template`, `description_template`, `playlist_id`), **including
the placeholder names**, and are stored under those keys -- so a roster
fetched from the server arrives already configured, and a stream and
the later upload of the same match are named the same way.
`_announce_to` is the one addition, underscored because VME has no
notion of it.

Stored locally rather than written back to the server: the name is
wanted at the gym, and the server is not reachable from one.

#### 2. The templates and the playlist are actually used

`goLiveWithToken` renders the title and description from the team's
config instead of the two hardcoded strings, and
`YouTubeLive.addToPlaylist` files the broadcast after it is created.

Playlist attach is best effort. A deleted playlist or a typo in the id
is not a reason to stop a match going out; the video can be moved by
hand, and the log says it needs to be. Same call and same reasoning as
the backend's post-upload attach.

Both run **only for a new broadcast**, alongside the thumbnail: a
resumed one is already named, filed and announced.

#### 3. The phone sends the announcement

*(First built as a VME endpoint. Rewritten the same day -- the
original and why it was wrong are kept below, because the reasoning
that produced it was sound and the thing that overturned it is worth
remembering.)*

`Mail.send` posts to `gmail.googleapis.com` with the **same access
token that created the broadcast**. `gmail.send` joins the scopes
`YouTubeAuth` asks for, so it rides along on one consent sheet -- the
operator grants it where they are standing, in the app, rather than by
running a PowerShell script on another machine.

One message per recipient, not one with everyone in `To`: the list is
usually a single Google Group, but when it is not, the members are
parents' addresses.

Subject and body are base64 in UTF-8 rather than written literally.
Team names carry accents often enough, and a header with a raw
non-ASCII byte in it is a malformed message rather than a mildly wrong
one. A 403 mentioning scope is translated to "sign in again", because
Google's own wording ("insufficient authentication scopes") reads like
a bug.

Best effort, off the go-live path, each address tried on its own.

##### What the server version got wrong

The first cut added `gmail.send` to the **backend's** `SCOPES` and
relayed through `POST /api/live/announce`. The reasoning was credential
hygiene: the server already holds a Google refresh token, so no mail
credential would sit on a device in a kit bag. Three things sank it.

1. **It did not work where it was needed.** The announcement matters
   most at a venue -- which is exactly where the gym's Wi-Fi is least
   likely to reach a machine at home. The first real attempt logged
   `announce failed - HTTP 404`, because the running service predated
   the endpoint; but even once restarted, the dependency is backwards.
2. **The consent was in the wrong place.** Enabling a button on the
   phone meant running `yt-authorize.ps1` on the server.
3. **It would have broken uploads.** `Credentials.refresh()` checks the
   requested scopes against what the saved grant carries and raises
   `RefreshError: Not all requested scopes were granted` when they are
   not a subset. Adding a scope the token did not have would have taken
   **YouTube uploads** down at the next refresh -- not just email. That
   is the part worth remembering: on this client library, widening
   `SCOPES` without re-consenting is not inert.

`notify.py`, its router registration and `VmeClient.announce` are gone;
the backend's `SCOPES` is back to the two YouTube entries, with a
comment saying why it must not grow again without a re-consent.

#### 3b. (superseded) VME sends the announcement

`POST /api/live/announce` takes `to`, `subject` and `body`; the phone
composes the message and the server posts it. The phone is the only
thing that knows a broadcast started; the server is the only thing
that can send mail without credentials sitting in a kit bag.

The phone composes rather than sending facts for the server to format,
because the templates live on the phone -- which means the email
subject and the YouTube title cannot drift apart.

**One message per recipient**, not one with everyone in `To`. The list
is usually a single Google Group, but when it is not, the members are
parents' addresses and putting them in each other's inboxes is not
ours to do.

Sending reuses the **existing Google grant** rather than standing up a
second OAuth flow: `gmail.send` joins `SCOPES`, and
`youtube._build_service` was split so `load_credentials()` serves both
APIs.

> **This needs a one-off re-consent.** The saved token predates the
> mail scope, so sending answers **503** with exactly that sentence
> and tells the operator to re-run `scripts/yt-authorize.ps1`. Uploads
> keep working on the old grant until then.

`granted_scopes()` reads the scope list out of the **token file**, not
off a `Credentials` object: `from_authorized_user_file` takes the
scopes it is *given*, so a credential built with `SCOPES` would claim
the mail scope whether or not anyone consented to it.

On the phone the announcement is fire-and-forget on its own thread. A
gym whose Wi-Fi cannot reach the server is not a reason to interrupt a
broadcast that is working; the failure is logged, because the operator
cannot act on it courtside anyway.

#### 3c. The email is templated too

Subject and body join the title and description in team settings. They
take the fixture placeholders plus two of their own:

- `{title}` -- the stream title, already rendered
- `{url}` -- the YouTube watch link

Both exist only *after* the broadcast does, which is why they are
blank for the title and description templates: those are rendered
before there is a broadcast to have a URL, and the title is the thing
being produced rather than an input to itself. `StreamConfig.vars()`
makes that explicit by defaulting them to empty.

`render` now folds a placeholder map instead of taking four positional
strings, so adding the next placeholder is one line rather than a new
parameter on every call site.

The defaults spell the fixture out rather than leaning on `{title}`:

```
Subject: LIVE: {team} vs {opponent} - {tournament}

{team} vs {opponent} is streaming live now.

Tournament: {tournament}
Date: {date}

Watch: {url}
```

Most of these land on a phone lock screen, where the subject is often
the whole message anyone reads -- so it names who is playing and
where, not just that something started. The body repeats the fixture
instead of interpolating `{title}`, because a team that rewrites its
title template to something terse should not silently lose the detail
here too.

A stored config wins over these, and blank falls back to them, so
clearing a field is how a team returns to the default.

The preview renders the title and then feeds it into the subject as
`{title}`, which is what happens at go-live: a subject referencing it
is shown resolved rather than as a placeholder to hold in your head.
The text watchers had to move to the end of `onCreate` for this -- the
fields are `lateinit`, and the preview now reads two of them.

#### 3d. The keyboard covered the field being typed into

Reported against the distribution list, which sits at the bottom of
team settings. The cause is shared, so every screen with a text field
had it.

`android:windowSoftInputMode="adjustResize"` is declared on all of
them and **does nothing**: from targetSdk 35 the window no longer
resizes for the IME. The app draws edge-to-edge and is expected to
treat the keyboard as an inset, which `applySystemBarInsets` was not
doing -- and because it returns `CONSUMED`, no child could compensate
either. So the field sat under the keyboard with nothing scrollable.

The IME now folds into the bottom padding of the same listener:

```kotlin
val ime = insets.getInsets(WindowInsetsCompat.Type.ime()).bottom
v.setPadding(bars.left, bars.top, bars.right, maxOf(bars.bottom, ime))
```

**`max`, not a sum.** The IME inset already spans the navigation bar
it covers; adding them leaves a gap the height of the nav bar floating
above the keyboard.

Shrinking the viewport does not move the caret into it, so the
listener then asks the focused view to bring itself on screen --
posted rather than immediate, because a scroll parent cannot honour
that until it has been laid out at its new height.

On a `ScrollView` (team settings, settings, import, home) the viewport
shrinks and the content scrolls. `MatchSetupActivity` has no
`ScrollView` -- its buttons are held at the bottom by a weighted
spacer, which a `ScrollView` would break -- but its fields are near
the top, so the padding alone lifts them clear. Worth revisiting if
that screen ever grows.

#### 4. Tournament: a name, and a list

The field was labelled "Tournament folder" and was free text. It is
now **"Tournament"** with a **Pick** button listing the tournaments
already on this phone, most recent first.

Derived from the matches on disk rather than kept as its own list: a
tournament exists precisely because a match is in it, so there is
nothing to keep in step and nothing to clean up when the last match of
one is deleted.

Typing stays possible -- the first match of a new tournament has
nothing to pick from. But by the second match of the weekend the name
is in the list, and a tournament typed two ways is two folders in VME,
which is only discovered once the matches are split across them.

#### Verified

`/api/live/announce` answers 400 on no recipients, 400 on an empty
message, and 503 with the re-consent sentence on a token that lacks
the scope -- which is this machine's actual state. Gated backend lint
clean; the phone builds.

**Verified on hardware.** A full go-live ran end to end:

```
auth: asking for consent...            <- gmail.send on the same sheet
auth: got an access token
live: bound. https://www.youtube.com/watch?v=LCkHymTbEus
live: thumbnail set (53 KB)
live: added to playlist                <- new
live: announce failed for ... HTTP 403: Gmail API has not been used
      in project 706103051598 before or it is disabled
```

That last one was not a code fault: **granting the scope and enabling
the API are different things.** The consent sheet asks the *user* for
permission; enabling Gmail in Cloud Console authorises the *project*
to call it at all. Enabled it, and the announcement sends.

Worth keeping in mind for the next Google API this app reaches for --
the error is clear but arrives from a direction that looks like an
auth bug and is not.

#### Files changed this round

```
android/.../data/Models.kt                 StreamConfig
android/.../data/Storage.kt                youtube block round-trip
android/.../ui/TeamSettingsActivity.kt     new
android/.../ui/HomeActivity.kt             team row opens settings
android/.../ui/MatchSetupActivity.kt       tournament label + picker
android/.../ui/MatchLiveActivity.kt        templates, playlist, announce
android/.../YouTubeLive.kt                 addToPlaylist
android/.../data/VmeClient.kt              announce()
android/app/src/main/AndroidManifest.xml   TeamSettingsActivity
backend/app/api/notify.py                  new
backend/app/main.py                        notify router
backend/app/upload/youtube.py              gmail scope, load_credentials
```

### Courtside iteration round 18 -- **VME integration: ticks, import, nudge**

The phone tags events against a wall clock and never sees the footage.
VME addresses events by clip and frame. This round is the bridge.

#### 1. Events carry a moment as well as a position

`MatchEvent.at_ms` -- Unix milliseconds -- alongside `clip_id` and
`local_frame`.

**Frames stay the rendering truth.** The renderer, frame map, cuts,
reels and overlays all work in frames, and still do. Ticks are the
*timing* truth: the thing that survives clips being re-probed,
reordered, replaced, or never having existed. Rewriting the render
path to work in ticks would have been a far larger and riskier change
for no gain, so the two live side by side and `reproject_event` is the
single place that keeps them in step.

New on `Match`:

- `timeline_epoch_ms` -- wall clock of global frame 0, taken from the
  first clip that knows when it started recording and walked back past
  the clips in front of it. **None** when no clip has a recording time:
  a match with no anchor to the world gets no fabricated one.
- `clip_start_ms` -- a clip's own recording time, or a projection from
  the epoch for clips ffprobe could not date.
- `event_time_ms` / `project_ms` -- the two directions.
- `covers_ms` -- was anything recording at that moment? `project_ms`
  always returns a position, so this is how a caller tells an exact
  placement from a rescued one.
- `backfill_event_times` -- runs on load. Legacy events gain a time
  derived from their frame position and their clip's recording time.
  Only fills what is missing, so an existing value is never
  second-guessed.

**The gap rule.** An event that lands between clips -- the camera was
stopped, so no footage of it exists -- is pulled forward to the first
frame of the next clip, the earliest place it could be shown. Before
the first clip is the same case; after the last clip lands on its
final frame, since there is nothing later to pull it to.

#### 2. `POST .../events/import`

Takes the phone's event log: `type`, payload fields inline, `_at` in
Unix milliseconds. The phone's own `clip_id` / `local_frame` are
placeholders for footage it never had, so they are dropped and the
projection decides.

Reports `imported`, `snapped` (landed in a gap) and `skipped` with a
reason per event, because "12 of your events happened while the camera
was stopped" is worth knowing and an unknown event type should not
fail the other 200.

Refuses rather than guesses when the match has no clips, or when none
of them has a recording time -- there is no mapping to make, and a
plausible-looking wrong answer is worse than an error.

Not idempotent: `replace: true` is how a re-import is meant to be
done. It keeps clip transitions, which are structural rather than
tagged.

#### 2b. Events can arrive before the footage

The order the work actually happens in: the phone is finished the
moment the match is, while the card is still in the camera. So an
import against a match with no clips - or with clips ffprobe could
not date - is the **normal** case, not an error.

Such events are stored with their `at_ms` and **no frame position**:
`clip_id` empty, so nothing downstream mistakes them for a real
placement. `global_frame` serialises as null, the editor already
renders that as an em dash, and the game-state fold skips them,
which is correct - they are not on the timeline yet.

`Match.place_pending_events()` picks them up from
`scanner.reconcile_clips`, which is the moment clips and their
recording times first exist. Idempotent, and it only ever touches
events with no valid clip, so one the operator has since nudged by
hand is left where they put it.

`_sort_events` grew a third rank for this. Placed events sort by
frame as before; parked ones sort *after* them by `at_ms`. Without
that rank an unplaced log collided on a single key and kept whatever
insertion order it had - right by luck on a first import, wrong on
the second.

The import reports `pending` alongside `imported` and `snapped`.

#### 2c. Playback is held while the libero prompt is open

The rotation rule fires on a scoring event, so the operator is almost
always mid-playback when the prompt appears. Footage running on behind
an unanswered question is how it gets dismissed without being read --
and a libero left in the front row is a lineup that is wrong from
there on.

`VideoPlayer` takes a `playbackLock: string | null`. When set it
pauses, refuses to start, disables the three play buttons, and shows
the reason over the video -- amber, because nothing is wrong,
something is waiting.

Pausing on the way in is only half of it: the hotkeys and the buttons
stay mounted, so `playAt` checks the lock too, which is the single
gate all four playback hotkeys and the rate hot-swap pass through. The
clip-switch resume path reads the lock from a ref, because it fires
from a closure captured when the src changed rather than on the render
that raised the lock.

**Stepping stays available.** Frame and second steps do not run away
from the operator, and looking at the play either side is often how
the question gets answered.

The lock is *derived* from `subReason` rather than tracked separately,
so the two cannot disagree. It clears when a player is picked and when
the picker is cancelled -- and deliberately **not** when the
substitution POST fails, because `clearOverlays` runs after `await
onCreate`: the question is still open, so the hold stays.

#### 3. `POST .../events/{id}/nudge`

`{seconds: ±1}`. Moves by **wall clock** where the match has recording
times -- a second later is a second later whatever clip it lands in,
including across a boundary -- and falls back to **frames** where it
does not. Refusing on legacy matches would have made the buttons dead
on exactly the matches most likely to need hand-correcting.

In the editor, `-1s` / `+1s` sit on each event row between the summary
and the delete button, dimmed until the row is hovered or selected:
used occasionally to correct a tag, and not competing with the summary
in a list of 200 rows.

`PATCH` also learned `at_ms`, and now recomputes the moment when
frames move by hand -- otherwise the two addressings drift apart
silently, which is the failure this whole round exists to prevent.

#### 4. Send from the phone

A **Send** button beside Export on the home screen. Confirms first,
naming the destination and the event count, and offers **Add** or
**Replace** -- the import is not idempotent and a double tap on a gym
connection would otherwise double every event in the match.

`VmeClient.post` is new: JSON body, Basic auth like the reads, a 30s
read timeout (an import rewrites match.json), and FastAPI's `detail`
pulled out of the error envelope, because that is the sentence worth
putting on a phone screen.

#### Verified

An end-to-end test against a throwaway media root, with two clips and
a 50-second gap between them:

```
# footage first
imported 4  snapped 1  skipped 2 (unknown type, no timestamp)
placement: 60, 150, 300 (snapped out of the gap), 390
nudge +1s: 390 -> 420      nudge -2s: 420 -> 360
nudge -30s across the gap -> clip B frame 0 (global 300)
replace -> event log is 2 events
undated clips -> parked 4, none placed

# events first, footage later (real ffmpeg file, creation_time read back)
no clips -> imported 3, pending 3, all global_frame null, time-ordered
clip adopted on next open: 300 frames @30, start 1700000000
placed at 60, 150, 240 -- 2s, 5s, 8s in
persisted to match.json and stable across reloads
```

Also checked: projection round-trips (frames -> ms -> frames), the
epoch is the first clip's recording time, backfill is a no-op on its
second pass, and a match with no recording times yields no epoch and
no projection.

**Not verified:** any of it against real footage, and the phone's
Send button has not run once -- the handset dropped off wireless
debugging before this build could be installed.

#### Files changed this round

```
backend/app/domain/events/base.py      MatchEvent.at_ms
backend/app/domain/match.py            epoch, projection, backfill, covers_ms
backend/app/api/schemas.py             at_ms, NudgeEventIn, ImportEventsIn/Out
backend/app/api/helpers.py             serialize at_ms
backend/app/api/events.py              import + nudge, at_ms on create/patch
frontend/src/types/api.ts              EventDto.at_ms
frontend/src/api/client.ts             nudgeEvent, importEvents
frontend/src/components/EventList.tsx  -1s / +1s per row
frontend/src/routes/MatchEditorPage.tsx nudgeEvent handler
frontend/src/styles/index.css          .event-nudge
android/.../data/VmeClient.kt          post(), importEvents()
android/.../ui/HomeActivity.kt         Send button
```

### Courtside iteration round 17 -- **thumbnail, broadcast reuse**

#### 1. A pre-roll thumbnail, generated on the phone

`overlay/StreamThumbnail` is a Canvas port of
`backend/app/render/thumbnail.py`'s landscape layout, so a channel's
live streams and its uploaded matches read as one set: two
team-coloured wedges meeting at a slanted seam (the scoreboard's
lean, `SEAM_TOP` 0.56 / `SEAM_BOTTOM` 0.44), a circular badge each
side at 0.13 and 0.87 of the width, and a neutral **VS** plate in
the middle with both names under it. Never a score -- it goes up
before the first whistle.

Specifically VME's *flat* variant. VME darkens a frame lifted from
the render and fades the wedges over it, which is what makes an
uploaded match look like that match; there is no footage yet here,
so the colours are the whole design -- exactly what
`_draw_wedges(flat=True)` produces when VME has no backdrop either.

**No opponent crest** falls back to the same shape VME's
`_badge_disc` draws: white ring, team-coloured field, initials
shrunk to fit. `abbreviate` takes initials from a multi-word name
("Bellevue Storm" -> BS) and the first three letters of a single
one ("Eastlake" -> EAS). Digits survive, because age-group sides are
routinely "Test 3" or "Rivers 14U" and dropping the number merges
two teams the operator has to tell apart.

**Names shrink to fit.** There is ~294px between a disc's inner
edge and the centre gutter, and a two-word club name at the nominal
52pt wants ~450 -- so at the ported sizes a long home name ran
straight into its own badge. Both names take one size, chosen from
the longer of the two: two names at visibly different sizes reads
as a mistake.

Uploaded with `thumbnails.set` (its own connection -- upload host,
binary body) right after the broadcast is created, and **only** for
a new one. Best effort throughout: a thumbnail that fails to
generate or upload must never stop a broadcast starting, and
YouTube falls back to a frame grab.

#### 2. Start does not create a second broadcast

Stop, or a dropped connection, used to strand the audience on a
dead link: the next Start made a *new* broadcast with a new URL.
Broadcasts are created with `enableAutoStop = false`, so stopping
the encoder leaves one sitting in `live` rather than completing it
-- it was always resumable, nothing was resuming it.

The match now stores `youtubeBroadcastId` alongside the watch URL.
On Start, `YouTubeLive.isReusable` asks for the broadcast's
`lifeCycleStatus` and reuses anything that is not `complete` or
`revoked`; otherwise it creates a new one and says so in the log.
`bind` runs either way -- it is idempotent, and a resumed broadcast
may have been bound to a different key on a channel with several.

The id is stored rather than parsed back out of the watch URL: the
URL is a display string that has already changed shape once in this
project, and an id that silently stopped matching would resume the
wrong video.

A lookup that fails is treated as "gone", not as an error: the only
decision it feeds is whether to reuse, and "could not tell" has the
same safe answer as "it has ended".

#### 3. Game End closes the set it ended on

There is no such thing as a finished match with an unfinished set.
The warning round 16 added ("Current set 1-4 is unfinished") was
describing a state the operator could do nothing useful about, and
worse, the deciding set was then missing from the FINAL card --
which is what made that card unreadable.

Game End now records a real `set_end` ahead of the `game_end`, when
there is a score to close. Deliberately an event rather than a
change to how the engine folds `game_end`: VME replays this log, and
a phone that quietly meant "and also end the set" would render a
different match on import. The price is that Undo takes two taps
here, which is honest -- two things happened.

The confirmation now just lists the sets, including the one about
to be closed.

#### 4. Set scores on one readable row

The row was built by joining the scores into a single string with
four spaces between them, then shrinking the whole line to fit.
That shrinks the **gaps** along with the digits, so by three sets
the scores ran together into one long number -- the opposite of
what the row is for. One set, as in the screenshot that prompted
this, was simply cramped against the names above it.

`drawSets` now lays them out item by item with the gap as a
measured quantity (`SET_GAP_EMS`, 0.8 of the type size) scaled in
step with the text. At 1080p a five-set match comes to 915px of a
1024px budget, so **every score is drawn at one size** with nothing
shrunk -- which is what makes the row readable straight across.

Sizes and spacing:

- FINAL 1.45x the nominal footnote (down from 1.7, which left no
  air between the scores and the names).
- TIMEOUT 0.95x -- context under a live score -- but with the same
  proportional gaps, so "smaller" does not become "crammed".
- `ROW_GAP_FRAC` 0.28 -> 0.30, giving 47px clear between the names
  and the scores and a 39px bottom margin on a 324px card.

The fallback that printed the running score when no set had ended
is gone with it: item 3 means the last set is always in the list.

#### 5. Stop ends the broadcast; Start clears the key

A stream key carries **one** live broadcast at a time. Round 17
leaned on `enableAutoStop = false` to make a reconnect resume the
same video, and never ended a broadcast at all -- so the key stayed
occupied and the next match could not go out on it.

Two halves:

- **Stop completes the broadcast** (`liveBroadcasts.transition` to
  `complete`), releasing the key. Best effort on a background
  thread: an expired token or no signal at the end of a match costs
  only the automatic cleanup, because Start ends stale broadcasts
  anyway and Studio can do it by hand.
- **Start ends anything else that is live.** Every `active`
  broadcast on the channel whose title is not this match's gets
  completed first, with a log line naming it.

"Not this match" is decided by **title** -- opponent plus date --
not only by the stored id. The id is absent exactly when this goes
wrong: a reinstall, a second phone, or a broadcast left running
from the previous match on the same court.

This narrows round 17's reuse rather than undoing it. Resuming now
covers the case it was really for -- a dropped connection or a
battery swap, where Stop was never pressed and the broadcast is
still `active`. Press Stop and the next Start makes a new video,
which is what "stop" should mean.

#### 6. Spaces around the dash in a set score

`25-21` reads as one number at a glance; `25 - 21` reads as a
score. Each one gets about 19% wider, which a five-set FINAL
absorbs with a 0.97x shrink -- uniform, and not visible. The
timeout card's live score already had them.

#### Owed on hardware

- Stream, Stop, then Start again: a **new** watch URL, and the old
  broadcast showing as ended in Studio.
- Stream match A, then open match B and Start: A is ended
  automatically and B goes out on the same key.
- Kill the app mid-stream and Start again: the **same** broadcast
  resumes, because Stop never ran.
- End a match mid-set: the FINAL card lists every set including
  that one, all the same size.
- A five-set match: still one row, still unshrunk.
- Go live on a fresh match: the thumbnail appears on the YouTube
  watch page before any video does.
- Opponent with no crest: the disc shows their initials on their
  colour.
- Long club names: neither name touches its badge.
- Stop, then Start again: the **same** watch URL, the same video
  continuing -- not a second broadcast.
- Start on a match whose broadcast was ended from YouTube Studio: a
  new broadcast, with the log saying the previous one had ended.

#### Files changed this round

```
android/.../overlay/StreamThumbnail.kt     new
android/.../YouTubeLive.kt                 broadcastStatus, isReusable, setThumbnail
android/.../data/Match.kt                  youtubeBroadcastId
android/.../ui/MatchLiveActivity.kt        resume-or-create, uploadThumbnail
```

### Courtside iteration round 16 -- **sleep, logos, confirmations**

#### 1. The sleep bug was the app switching itself off

"Loses connection to YouTube when the phone goes to sleep" survived
round 14's `FLAG_KEEP_SCREEN_ON`, because that flag was treating a
symptom. The cause was four lines in the preview surface callback:

```kotlin
override fun surfaceDestroyed(holder: SurfaceHolder) {
    surfaceReady = false
    if (stream.isOnPreview) stream.stopPreview()   // <- killed the stream
}
```

The screen turning off destroys the `SurfaceView`'s surface.
Stopping the preview there tore down the GL pipeline the **encoder**
draws through, so the broadcast ended with the screen. No wake lock
could ever have fixed this: nothing was asleep, the app had switched
itself off.

The preview is only a monitor -- `startStream` renders to the
encoder's own surface -- so while streaming the surface is now
allowed to go and `attachPreview` puts it back when it returns.

Two further things were needed for a broadcast to survive a locked
phone, and they are the round-14 deferral finally paid off:

- **A foreground service** (`StreamService`), so Android does not
  freeze the process once it is backgrounded. Started when
  `startStream` is called, stopped on Stop and on `onDestroy`.
- **A partial wake lock**, held by that service. A foreground
  service alone does not keep the CPU up with the screen off.

The service deliberately does **not** own the camera, encoder or
stream: those stay on `MatchLiveActivity`, where the surface, the
overlays and the event log already are. It is a permission and
lifetime holder, nothing else.

Types are `microphone|connectedDevice` -- audio, because background
mic access has needed a typed FGS since Android 11, and the USB
capture adapter. **Not** `camera`: the video is a UVC device on the
USB host API, not the platform camera service, and that type would
demand a runtime CAMERA grant this app never asks for. The typed
`startForeground` is wrapped, falling back to an untyped one, so a
denied microphone permission costs the type and not the broadcast.

#### 2. FINAL shows the set scores, not the tally

`3 - 1` was a summary of the line underneath it, and the line
underneath is the half worth having: `25-21  23-25  25-19` says who
won *and* how it went. The centre figure is now a dash -- it keeps
the two names apart without asserting a number the scores already
carry -- and the footnote is drawn at 1.7x, shrinking to fit when a
five-set match would otherwise run past the card edge.

A match ended with no `set_end` recorded falls back to the current
score, because a blank card is worse than an unfinished one.

#### 3. Start and Stop confirm

Both are one tap from ending or starting a public broadcast. Start
names the broadcast it is about to create; Stop says plainly that
the YouTube video cannot be resumed, and that scoring continues
either way.

#### 4. Team badges

**Home** comes from VME with the roster: one more GET at import
(`/api/teams/{team}/logo`), cached beside the player photos as
`team-logo.img`. Null on 404 or failure, like the photos -- a
missing badge must never fail an import.

**Opponent** is picked from the phone. There is no opponent roster
on the server to import one from, and the badge is usually on a
draw sheet or a club website minutes before the first whistle. The
team editor's "Logo..." button commits the name and colour first
(so opening the picker is not a silent way to lose them) and then
opens the gallery. The image is **copied** into the cache, not
referenced: a `content://` URI is a grant to this task, not a
durable handle, so a match reopened tomorrow could not have read it.

Cached per match (`_opp_<slug>`), not per team -- the same club can
turn up in several matches with a badge grabbed separately each
time. The prefix cannot collide with a team slug, because VME slugs
never begin with an underscore.

**Drawn** in three places, aspect preserved inside a square box
rather than stretched to fill it (club crests are all shapes, and a
squashed one is more noticeable than a small one):

- **Scoreboard**, inside each team's coloured section, before the
  name. The section widths in `init` now include the badge, so the
  text still lands where the width was reserved.
- **COMING UP / TIMEOUT / FINAL**, outboard of each team name,
  where the card's margins were empty. Skipped rather than clipped
  when a long name has eaten the margin: half a crest off the card
  edge reads as a rendering fault, no crest just reads as no crest.

Like a rename, a badge added **mid-stream** reaches the centre card
immediately but the scoreboard only at the next stream start -- the
bar's sections are sized around it in `init`, and `setScale` on an
attached filter blanks it. The log says so when it applies.

#### 5. Card alignment -- **from a screenshot of the live card**

Two faults, both vertical, both visible on the COMING UP card:

**Badges sat about half a cap-height below their team name.**
`drawText` takes a *baseline*; a bitmap has only a centre. The card
handed the same `y` to both, so text and badge were never on the
same line. Every row is now placed by optical centre through a
`drawAt` helper -- `cy - (ascent + descent) / 2` -- which is what
`ScoreboardOverlay.drawTextLeft` had been doing correctly all
along.

**The block hugged the top of the card.** Rows were at fixed
fractions chosen for three of them (title, names, footnote), but
COMING UP has no footnote and drew only two, leaving the bottom
third empty. Rows are now derived from one `ROW_GAP_FRAC` and
centred on the card: three rows at 0.22 / 0.50 / 0.78, two rows at
0.36 / 0.64. Same spacing either way, and both layouts balanced.

Horizontal alignment was already correct -- both names sit exactly
`0.14 * cardW` from the centre line -- so nothing there changed.

#### 6. Cropping the opponent badge

A badge picked out of the gallery is rarely a badge: it is a
screenshot of a draw sheet, or a club photo with the crest in one
corner. The overlays draw it in a square box with aspect preserved,
so an uncropped 16:9 screenshot became a thin strip with the crest
invisible somewhere inside it.

`ui/LogoCropActivity` sits between the picker and the save. Fixed
square frame, image moves behind it -- the avatar-cropper model,
which needs no drag handles. Pinch to zoom, drag to pan, and the
image is clamped to always cover the frame, so a crop with a
transparent edge cannot be produced by accident and there is no
invalid state to warn about.

Hand-rolled, at about 200 lines, over the two alternatives:
`com.android.camera.action.CROP` is not a platform API -- it is
whatever the OEM gallery happens to expose, and on a phone that
does not expose it the feature would silently do nothing -- and a
crop library is a dependency and a few hundred KB for one screen
used once per match.

Details worth keeping: the source is decoded downscaled (1600px
longest edge) because a 12MP photo held next to the output is the
only allocation on that screen big enough to matter; output is
**PNG** at 512px, since crests routinely have transparent
backgrounds that JPEG would fill with black; and the result crosses
back as a file path rather than a Bitmap extra, which at 512px
would be near the ~1MB Binder transaction limit.

The crop lands in `cacheDir`, which Android may clear at any time,
so `storeOpponentLogo` copies it into `filesDir` and deletes the
temporary -- the same durability reason the `content://` URI could
not be kept.

#### Owed on hardware -- the important one this round

Everything above compiles, installs and launches clean, but the
sleep fix is the whole point of the round and **none of it is
verified**:

- Go live, lock the phone, leave it a few minutes: the YouTube
  broadcast keeps receiving video and audio.
- Unlock: the preview comes back rather than staying black.
- Switch to another app and back, same.
- The "VME is live" notification appears, and returns to the match
  when tapped.
- Stop, and the notification goes with it.
- Badges: import a team with a logo in VME; pick an opponent badge
  from the gallery, crop it, and confirm both on the bar and on all
  three cards.
- Cropper: a wide screenshot and a tall one both start covering the
  frame; pinch-out cannot shrink the image inside it; Cancel leaves
  the previous badge alone.

#### Files changed this round

```
android/.../StreamService.kt               new
android/.../AndroidManifest.xml            service, WAKE_LOCK, FGS perms
android/.../data/Models.kt                 Roster.teamLogoPath, localLogoPath
android/.../data/Storage.kt                logo round-trip
android/.../data/PhotoCache.kt             storeLogo, logoFileFor, opponentKey
android/.../data/VmeClient.kt              fetchTeamLogo
android/.../ui/RosterImportActivity.kt     fetch the badge at import
android/.../ui/MatchLiveActivity.kt        picker, confirmations, surface fix
android/.../overlay/ScoreboardOverlay.kt   badge in each section
android/.../overlay/CardOverlay.kt         badges, FINAL footnote
```

### Courtside iteration round 15 -- **team edit, button chrome, timeout**

Seven items of feedback on the round-14 build. Two of them were
things round 14's log claimed and the tree did not have; both are
noted as such below rather than quietly fixed.

#### 1. Overlays: 10% transparent, not 15%

`OverlayAlpha.OVERLAY_ALPHA` 0.85 -> **0.90**. One constant, one
`DST_IN` pass, so the scoreboard, the pop-ups and the centre card
cannot drift apart.

#### 2. Tapping the opponent card did nothing -- **was never built**

Round 14's log listed "opponent card is editable". It was not:
`buildAwayCard` painted a card and attached no listener, and the
`cardContainer(name, colorHex)` call that looked like an editor
only sets the border.

Now the away card is clickable and opens `editTeam(Side.Away)`: a
name field plus a row of eight swatches, ringed in white on the
current pick (a tick disappears against the lighter colours). The
buttons inside the header keep consuming their own taps, so only
the card body and the name open it.

**The home card deliberately has no equivalent.** Its name and
colour come from the team roster imported from VME; editing a
local copy here would diverge from the record the editor re-reads
on import. Fix it in VME, re-import.

##### Repainting without a rebuild

`setContentView(buildUi())` would have been the easy way and is
not available: it tears down the preview `SurfaceView`, which is
bound to the encoder for as long as the stream runs. So the views
whose text or colour comes from a team are held in a `TeamChrome`
per side -- card, swatch, name, +1, score-header label -- and
`refreshTeamChrome()` repaints them on every refresh. Five view
writes per side; running it unconditionally means a rename cannot
leave one label behind.

##### What the live overlays can take

Split, because the two overlays differ:

- **`CardOverlay`** takes names *and* colours. Nothing about it is
  derived from the names -- they are centred at draw time, the
  card is a fixed fraction of the frame -- so `setTeams()` plus a
  redraw is the whole update.
- **`ScoreboardOverlay`** takes colours only (`setTeamColors()`).
  Its `barW` is *measured from the two team names* in `init` and
  becomes a `setScale` at `attach`, and `setScale` on an attached
  filter reproducibly blanks it. Rebuilding the filter is the
  other route, and `addFilter` after `startStream` silently drops
  filters on the S24. So a **rename reaches the bar at the next
  stream start**, and the log says so when it applies.

Before Start neither matters: both overlays are built at Start and
pick the edit up for free. That is also the case that actually
happens -- the opponent gets named off a draw sheet in warm-up.

#### 3. Emoji and colour on the buttons

Emoji first, word second: at courtside speed the glyph is what the
eye lands on, and the word is for the tap after, when the operator
is checking rather than reaching.

```
💥 Kill   🎯 Ace        attack     #7E3B45
🤲 Dig    🤿 Dive
🧱 Block                defence    #2F5B7A
⭐ Highlight             extra      #7A5C1E
🏐 Ball Served          serve      #2E6B45
🔄 Replay  ⏱️ Timeout    neutral    #3A3F4B
▶️ Game Start  ⏹️ Game End  🏁 End Set   lifecycle  #4A3A6B
🦺 Liberos              libero     #1F6B6B
```

Colour is applied through `buttonFill()`, a ripple over a rounded
fill -- **not** `setBackgroundColor`, which is the round-13 bug:
it replaces the whole background drawable and takes the pressed
state with it, leaving a flat shape that reads as disabled.
Disabled still reads correctly because the gate dims with `alpha`,
not with the drawable.

#### 4. End Set and Game End prompt

Both go through `confirm()`. Undo rolls either back, but a set end
clears the rotation and a game end stops scoring and changes what
is on the video -- noticing the mis-tap is the slow part, not
repairing it. Each message carries the score, so the prompt is
also a check: *"RIVERS takes it 25-21. Scores reset and the
rotation clears."*

#### 5. A timeout freezes the surface

Every control greys out except **Ball Served** and **Timeout** --
play has stopped, so nothing is taggable, and those two are the
only taps that mean anything. Either ends the timeout: Ball Served
through the existing clear in `record()`, Timeout through
`endTimeout()`. The Timeout button relabels to **End Timeout**
while one is up, so tapping it is an obvious thing to do.

This deliberately overrides the within-rally rule that greys Ball
Served after a serve: if a timeout was called mid-rally, Ball
Served has to stay reachable, because it is half the way out.

`endTimeout` records no event -- the Timeout is already in the log
and that is the thing that happened. How long the card stayed up
is presentation, which the renderer decides on import.

#### 6. The timeout no longer expires on its own

Was a 30 s `postDelayed`. Now a plain `timeoutShown` flag with no
clock: the operator is watching the court and the referee, not a
phone, and a card that vanished on a timer while the teams were
still huddled was worse than one that waits for a tap. `TIMEOUT_MS`,
`timeoutExpiry` and the activity's `uiHandler` are all gone with it.

#### 7. The timeout card lists every set -- not "sets 1-0"

`setsFoot` is replaced by `setScoresFoot`, shared with FINAL. A
running tally says less than the scores it comes from -- `25-21`
tells a viewer both who leads and whether it was close -- and
giving one card the summary and the other the detail made the pair
read as two unrelated designs. Null in the first set, where there
is nothing to list.

#### 8. Faces in the player picker -- **was never built**

Round 14's log listed "player photos in the sub picker". The
photos were fetched and cached at import, and `PopupOverlay` drew
them on the video, but the picker called `setItems`, which takes
strings and nothing else.

Now a `BaseAdapter` builds each row: the cached photo cropped to a
circle via `RoundedBitmapDrawableFactory`, or the same
team-coloured disc the pop-ups fall back to, then jersey and name.
Decoded at the size drawn (~40dp) for the same reason the pop-up
renderer does it -- twelve multi-megapixel photos decoded full-size
is the one place this list could run the phone out of memory.
`convertView` is deliberately not recycled: a roster is a dozen
rows, all on screen, so there is nothing to recycle and reuse
would mean clearing a stale bitmap on every bind.

#### 9. Gaps between buttons

The coloured rounded fills from item 3 were butting together into
one slab -- the corners read as notches cut out of a single bar
rather than as separate buttons.

A single density-scaled `gapPx` (6dp) now separates every adjacent
control: between cells in `rowOf`, under each row, between the six
rotation slots and between the grid's two rows, under Ball Served,
and between the compact buttons in the team header. Set inside
`rowOf` on the row's own `layoutParams` rather than at each call
site -- `addView(child)` keeps the child's params when it has
them, so every caller gets the gap without passing anything.

The grid gaps are also what round 14 asked for as "borders between
buttons in player select mode": when the grid is armed, each lit
slot has to read as its own target, and a continuous green band
does not.

Two knock-on changes so the grid does not look like stock Android
dropped into a themed screen: the rotation slots take the same
rounded fill (`BG_SLOT`, darker than the action buttons -- the grid
is a display that happens to be tappable, not a row of commands
competing for the eye), and `tintButton` now applies the armed
highlight through `buttonFill` instead of `setBackgroundColor`, so
arming an action no longer visibly squares off half the screen.

#### 10. A bigger colour palette

Eight swatches in one row became **42 in a grid**, six per row,
one hue family per row -- scan down for the family, across for the
shade. A single row could not have stretched: at that width the
swatches stop being tappable, and the eye cannot find a shade in a
long strip anyway.

The last row is the one that will get used most: club kits are
white, black or grey about as often as they are coloured, and a
palette with no near-neutral option forces a wrong answer.

Still a fixed grid rather than a colour wheel -- the value has to
land as a hex string VME will accept, and a wheel asks an operator
to fiddle with a gradient between rallies.

The selection ring now flips between black and white on the
swatch's luminance (`ColorUtils.calculateLuminance`), because a
white ring on the white swatch is not an indicator. Unselected
swatches carry a faint hairline so the pale ones have an edge
against the dialog.

#### Owed on hardware

- Opponent card: rename + recolour lands on the card, the score
  header, the +1 button and the centre card. Mid-stream, the bar
  changes colour and logs that the name waits for the next start.
- Armed mode still restores the +1 button's colour after a rename
  (`defaultBackgrounds` is re-synced in `refreshTeamChrome`).
- Timeout: everything but the two buttons greys; both end it; the
  card stays up indefinitely until tapped.
- Picker: photos for players who have them, discs for those who
  do not, on both home and opponent rosters.

#### Files changed this round

```
android/.../overlay/OverlayAlpha.kt        0.85 -> 0.90
android/.../overlay/CardOverlay.kt         setTeams, setScoresFoot
android/.../overlay/ScoreboardOverlay.kt   setTeamColors, public names
android/.../ui/MatchLiveActivity.kt        everything else
```

Installed on the S24 over wireless debugging (`android/deploy.cmd`);
launches clean, no `AndroidRuntime` errors, UVC camera negotiates
1920x1080@30 as before.

### Courtside iteration round 14 -- **liberos, timeout card, sleep**

The session that built this round was cut off mid-edit, so the
first half below is reconstructed from the tree rather than from
notes. Everything compiles; nothing in this round has been on the
phone yet.

#### Landed in the interrupted session (compiler only)

- **All overlays translucent to the same degree** --
  `overlay/OverlayAlpha.kt`, the `DST_IN` pass from round 13
  applied to pop-ups and cards too. Landed at 85% (round 13's
  figure for the scoreboard); **now 90%**, corrected on review --
  85% was reading as too washed out over live play. One constant,
  `OverlayAlpha.OVERLAY_ALPHA`, so the scoreboard, pop-ups and
  the centre card cannot drift apart.
- **Popup test section removed**, buttons and code.
- ~~Opponent card is editable~~ -- claimed here, never built.
  See round 15, item 2.
- **MATCH section relayout** -- Ball Served full width at 72dp
  with 18sp text; Replay | Timeout on the next row; Game Start |
  Game End | End Set below.
- **Centre card** (`overlay/CardOverlay.kt`) generalised from the
  round-11 final card: COMING UP with both team names before
  Game Start, TIMEOUT with the live score after a Timeout event,
  FINAL after Game End. One card, three states. (The 30 s timer
  and the "sets 1-0" footnote are both gone -- see round 15.)
- **Player photos** fetched and cached at import
  (`data/PhotoCache.kt`) and drawn on the video by `PopupOverlay`.
  Not in the sub picker, despite this log's first claim -- see
  round 15, item 8.
- **Libero designation** -- a "Liberos..." multi-select at the
  bottom of the events pane; `(L)` suffix on the grid and in
  pickers. Landed as a per-player flag on the embedded home
  roster; moved to the match in item 4 below.
- Not found in the tree: borders between grid buttons in armed
  mode. Still open.

#### Finished here

##### 1. Libero rotation rule

A libero may only play the back row. When a side-out rotates one
into P4 they have to come off, and the player who comes back on
is the one they replaced -- every six rotations, while the
operator is watching the court. So the swap is automatic.

**Engine.** `GameStateEngine.compute(events, liberos)` takes the
libero jerseys alongside the events: who is a libero is roster
knowledge, not something the event log records, and baking a
flag into each substitution payload would have given VME's
importer a field it never asked for. `onSubstitution` now
maintains `GameState.liberoReplacements` (libero -> who they came
on for): set when a libero comes on for a non-libero, dropped
when a libero goes off by any route, and left empty when a
libero is placed into an empty slot at set start. Cleared on set
end along with the positions. Because it is folded from the log
it survives Undo without any extra code. `frontRowLibero()`
returns the first libero found in P4 / P3 / P2.

**Activity.** `enforceLiberoRule(type)` runs at the end of every
`record()`. Only Score / Kill / Ace can rotate, so only they are
checked -- a direct substitution *into* the front row is the
operator's explicit choice, and yanking it straight back would
fight them. On a hit with a remembered partner who is not
already on court, an ordinary substitution event is recorded at
the libero's position. Otherwise the picker opens with liberos
filtered out (`pickPlayer(excludeLiberos = true)`) -- the one
thing that cannot fill a front-row slot is the other libero.
Cancelling the picker leaves the libero where they are; nothing
is blocked, the log just says so.

Why a plain substitution event and not a new type: VME replays
it, Undo removes it, `match.json` needs nothing new, and the Sub
pop-up it raises is exactly what VME would draw for the same
event in a finished match.

##### 2. Phone sleeping killed the stream -- **only half fixed here**

> Round 16 found the actual cause: `surfaceDestroyed` was stopping
> the preview, which tore down the encoder's GL pipeline. The flag
> below is still wanted, but it was never going to be enough.


The phone sits on a tripod and is only touched between rallies.
After the screen timeout the activity pauses and the OS
eventually freezes the process -- there is no foreground service
keeping it alive. `FLAG_KEEP_SCREEN_ON` on the live activity's
window stops the sleep in the case that actually happens
(app in front, nobody touching it). It does not cover switching
apps or the power button; a foreground service would, and is
the next step if that turns out to matter. Deliberately not done
now because the UVC camera is bound to this activity's lifecycle
and the service would have to take that over.

##### 3. `(L)` everywhere a jersey is shown

`positionLabel` and `pickPlayer` now go through
`Match.jerseyLabel(number)`, so the grid and every home picker
mark liberos. The opponent picker deliberately does not: the
tag is a home-team fact, and an opponent wearing the same
number as our libero must not inherit it.

##### 4. Liberos are a match property, not a roster one

First cut put `is_libero` on `Player` -- on the phone's embedded
home roster and on VME's team roster. Wrong on both counts:

- Who wears the libero jersey changes from tournament to
  tournament. A roster flag would silently rewrite the rule for
  every match already tagged.
- VME ignores the phone's `home_roster` on import and loads the
  real one from the team folder, so a per-player flag in
  `match.json` would have been lost on the way in.

Now `Match.liberos: [7, 12]` on both sides, written as `liberos`
in `match.json`. On the phone, `Player.isLibero` and
`Player.jerseyLabel` are gone; `Match.liberos: Set<Int>` and
`Match.jerseyLabel(number)` replace them, and `pickLiberos`
edits the set instead of the roster. The engine was already
taking a `Set<Int>`, so it did not change.

#### Same rule in VME

**Backend.** `Match.liberos: list[int]` on the dataclass, in
`to_dict` / `from_dict` (missing or `null` -> empty), on
`MatchOut`, and on `UpdateMatchIn` as `Optional` so a PATCH that
does not mention it leaves it alone; an explicit `[]` clears it.
`patch_match` dedupes preserving order and does not check the
roster -- the phone adds ad-hoc jerseys. No state-machine
change: the swap is an ordinary substitution event, so a
finished match replays identically with or without the list.
`Player` is untouched.

**Frontend.** `MatchDto.liberos`. A new `Controls/LiberoPicker`
replaces the lineup grid the same way `RosterPicker` does, as a
toggle grid with Save / Cancel; it is opened from an **L**
button in the home card header next to first-serve, which
reads `L x2` once liberos are set so the state is visible
without opening it. Save goes through
`MatchEditorPage.setLiberos` -> `PATCH {liberos}`.
`Controls/state.ts` gains `jerseyLabel(jersey, liberos)`,
`frontRowLibero(positions, liberos)` and
`liberoReplacements(events, liberos, uptoFrame)` -- the same
fold as the phone's, using each event's server-computed `state`
as the *pre*-state of the next. `LineupGrid` and `RosterPicker`
take `liberos` for the `(L)` tag; `RosterPicker` also takes
`excludeLiberos` and a `reason` line.

In `ControlsPanel` a scoring commit stashes its frame in
`liberoCheckFrame`; an effect on `data.events` then checks the
state at that frame. Remembered partner -> auto-commits a
substitution at the same frame (the backend sort is stable and
the new event appends, so it lands after the score). Nobody
remembered -> `RosterPicker` opens for that position with
liberos hidden and a reason line. An effect rather than a check
inline in `commit` because `commit` awaits `onCreate`, but the
reloaded `data` only arrives on the next render.

**Verified:** `tsc -b` clean; `liberos` round-trips through
`Match.to_dict` / `from_dict`, `UpdateMatchIn` and `MatchOut`;
ruff clean. **Not verified:** any of it in a browser.

#### Owed on hardware / in a browser

- Libero on for P5, home wins a side-out three times: swap fires
  on the third, pop-up reads `#L -> #X`, Undo removes the swap
  and the score together.
- Libero placed into an empty P5 at set start: rotation to P4
  opens the picker with both liberos absent.
- Screen stays on through a full set with no touches.
- VME: same two libero scenarios in the editor; the **L** picker
  survives Save and reload and the count shows on the button.
- Phone -> VME: export a match with liberos set, import it, and
  confirm the editor shows the same `(L)` tags.

#### Files changed this round

```
android/.../data/Models.kt                    Player.isLibero removed
android/.../data/Match.kt                     Match.liberos, jerseyLabel, JSON
android/.../data/Storage.kt                   is_libero no longer read/written
android/.../logic/GameStateEngine.kt          liberos param, pairing, frontRowLibero
android/.../ui/MatchLiveActivity.kt           enforceLiberoRule, keep-screen-on, (L) labels
backend/app/domain/match.py                   Match.liberos + JSON
backend/app/api/schemas.py                    MatchOut.liberos, UpdateMatchIn.liberos
backend/app/api/matches.py                    PATCH liberos
backend/app/api/helpers.py                    serialize liberos
frontend/src/types/api.ts                     MatchDto.liberos
frontend/src/components/Controls/state.ts     libero helpers
frontend/src/components/Controls/LiberoPicker.tsx    new
frontend/src/components/Controls/ControlsPanel.tsx   L button, picker, rule trigger
frontend/src/components/Controls/RosterPicker.tsx    (L), excludeLiberos, reason
frontend/src/components/Controls/LineupGrid.tsx      (L)
frontend/src/routes/MatchEditorPage.tsx       setLiberos
frontend/src/styles/*.css                     .libero-btn, .roster-chip.selected
```

### Courtside iteration round 13 -- **scoreboard polish, sub bug, icon**

#### 1. Scoreboard lifted and made translucent

The bar was bottom-flush because an early note in this doc
claimed `TranslateTo` had no Y-offset and no `BOTTOM_CENTER`.
The percentage `setPosition(x, y)` overload does both, so the
bar now floats `BOTTOM_MARGIN_FRAC` (3% of frame height, ~32 px
at 1080p) above the edge, matching VME's 30 px gap. Flush also
risked losing the bottom row entirely to TV overscan.

Drawn at **85% opacity** so the near end line shows through --
the bar sits right over it and a solid slab hides play.

Applied as a single `DST_IN` pass over the finished bitmap
rather than baking alpha into every paint: `DST_IN` keeps the
destination colour and multiplies its alpha by the source's, so
text and background fade *together* and stay mutually opaque.
Painting each element at 85% instead would let the bar
background show through the lettering.

Deliberately not `filter.setAlpha()`: that is also how `hide()`
works, and the two would fight.

#### 2. Substitution pop-ups named the wrong outgoing player

Subs read `#7 -> #7` -- every player appeared to sub for
themselves.

`popupSpec` resolved the outgoing player with
`gameState.homePositions[pos]`, but `gameState` is computed
from the event list, and by the time the pop-up fires the
substitution is already in that list. So the slot it read
already held the *incoming* player.

`record()` now snapshots state **before** appending the event
and threads it through `firePopup` / `popupSpec`. Taken
uniformly for all events, not just substitutions, because that
is what VME does -- its `overlay_effect_for_state` is handed
the pre-event state too.

#### 3. Grid and action buttons looked disabled

They rendered dim and flat but worked fine when tapped.

`setBackgroundColor` does not *tint* a Button -- it replaces
the entire background drawable, including the state list that
gives it its fill and its pressed/disabled appearance. So
clearing the armed highlight with
`setBackgroundColor(TRANSPARENT)` left a flat, unfilled button
that reads as disabled.

Now the stock drawable is captured on first tint and put back
on clear (`tintButton` / `restoreButton`), keyed on
`containsKey` so a never-tinted button is left alone and a
genuinely-null background still restores to null.

#### 4. Home: row opens, Export replaces Open, Delete shrunk

- **Tapping the row opens the match.** "Open" was a button for
  the action the whole row already implies.
- **Export took its slot.** Exporting happens after a match,
  possibly days later, from the list -- not while scoring. It
  is now the only route, since Stop stopped prompting for it
  in round 11. Removed from the live header, which frees room
  there too.
- **Delete is ~1/3 size**, muted red. Shrinking a Button needs
  `minWidth`/`minHeight` (and the `minimum*` pair) zeroed --
  padding alone does nothing against the platform's 88x48dp
  floor. Held at ~34dp rather than going smaller: a tiny target
  gets mis-hit *onto the row*, which now opens the match.
- **Both deletes now confirm.** Survivable when Delete was a
  deliberate full-size button; next to a tap-to-open row, an
  unconfirmed destructive action is a trap.

#### 5. App icon

Using `frontend/public/favicon.png` -- the blue/yellow VME
volleyball -- so phone and web read as one product.

No PIL or ImageMagick on this box, but ffmpeg is present and
handles PNG alpha, so the density set was generated with
`scale=N:N:flags=lanczos` (48/72/96/144/192 plus a 324px copy
for the adaptive foreground). The source is only 120px, so
xxhdpi and xxxhdpi are upscaled -- acceptable for a flat
graphic with no fine detail, and worth replacing if a larger
original turns up.

Adaptive icon for API 26+: white plate (a dark one turned the
ball's white panels into holes) with the ball at **60dp
centred on the 108dp canvas**. Only the centre 66dp of that
canvas is guaranteed visible -- launchers mask the rest into
circles and squircles -- so a full-bleed ball would have been
clipped into a flat-sided blob. Scaling is done in the
layer-list rather than by shrinking the PNG, so the bitmap
stays full resolution for the legacy icon.

### Courtside iteration round 12 -- **armed attribution, health, settings**

#### 1. Player events arm the grid instead of opening a dialog

Tapping Kill/Dig/Dive/Block/Highlight now **arms** the action:
every other control goes dark, the six rotation slots light up
green, and the next tap on a slot credits that player.

The modal jersey list was wrong courtside for two reasons. It
covered the score and the grid at the exact moment the next
rally was starting, so the operator had to look away from the
court to read it. And it listed the *whole roster* in jersey
order, when the player who just got the kill is almost always
one of the six on court -- whose on-screen position matches
where the operator just watched them standing. Tapping that is
close to muscle memory; finding a row in a sorted list is not.

Details:

- Re-tapping the armed action disarms it, so a mis-tap is
  backed out with the same button that caused it.
- The armed button stays lit and enabled purely so it can
  disarm; every other gated control is disabled, so a
  double-tap cannot queue a second action.
- Empty slots render dim and log `"Kill: P3 is empty"` rather
  than silently ignoring the tap.
- **Long-press a slot** still opens the full roster, so a bench
  player can be credited without subbing them on first. With
  nothing armed, long-press and tap both mean "substitute", as
  before.

#### 2. Ace is attributed automatically

An ace is by definition scored by the server, and the server is
already on screen in position 1. Asking was busywork. Ace now
records straight against `homePositions[1]` with no prompt at
all, falling back to the picker only when the rotation has not
been filled in -- the one case where we genuinely cannot know.

#### 3. Stream health in the header

`onNewBitrate` was writing the upload rate to the scrolling log
and nowhere else, which is the one number that answers "is this
actually going out". It now paints into the header, in the slot
Start occupies -- Start is a dead control while live, so it is
hidden for the duration and restored on Stop.

Colour-coded against the configured `VIDEO_BITRATE` rather than
an absolute number, since "healthy" depends entirely on what we
asked the encoder for: green above 80% of target, amber above
50%, red below that or at zero. Below half of target sustained
means the network is throttling us and the picture is visibly
degrading -- worth a red light courtside, where the operator
cannot see the stream itself.

#### 4. One tab button instead of two

Events and Preview shared two permanent header buttons. There
are only two panes, so which one you are on is obvious from
what is on screen; the button is now a single toggle labelled
with where it *goes*. The reclaimed width went to the health
readout.

#### 5. Dev spike removed

Deleted `MainActivity.kt`, its manifest entry and the Home
button. The courtside app has covered the spike's ground for
several rounds and it was the only `exported="true"` activity
besides the launcher.

#### 6. Settings page

New `ui/SettingsActivity.kt` + `data/Settings.kt`.

The backend URL, user and password were owned by
`RosterImportActivity` -- it drew the fields, read them, and
wrote them under keys only it knew. Fine while import was the
only screen that talked to the backend; not fine once photo
fetching joined it, because changing a password meant walking
into a task flow and deliberately not completing it.

- `data/Settings.kt` owns the three values. **Pref keys carried
  over verbatim** so devices that already had a server
  configured keep it -- a silent wipe would look like the
  setting "didn't save".
- Import now shows a read-only `Server: ...` banner; Home shows
  the URL under the title, in amber with "tap to set one" when
  unconfigured, since an unconfigured server makes both Home
  actions fail and the fix is not on the screen they fail on.
- Password is masked with both `inputType` and
  `PasswordTransformationMethod` -- `inputType` alone gets
  ignored by some IMEs, and the transformation method alone
  fails to hide the last typed character.
- **Test** button is the point of the page. Saving a URL tells
  you nothing, and every failure mode here (wrong port, backend
  down, Basic auth not enabled on IIS, typo'd password) is
  invisible until something uses it -- which should not first
  happen in the gym. Test does a real authenticated
  `/api/teams` round trip and translates the result:
  401 names the IIS-Basic-auth cause, 404 says "reached a
  server, but no VME API here", timeout asks about Wi-Fi.

Storage stays plain SharedPreferences, password included. This
is a single-purpose device in a kit bag; app-private storage is
already the meaningful boundary, and an operator locked out
courtside by a keystore migration failure is a worse outcome
than the one encryption would prevent.

### Courtside iteration round 11 -- **game gating, final card, slide animation**

Seven changes from courtside feedback.

#### 1. Scoreboard hidden until the game starts

VME's `GameStartEvent` / `GameEndEvent` carry a
`ScoreboardVisibilityEffect`, and we were ignoring it. Now
`pushOverlay` only calls `ScoreboardOverlay.update` while
`gameStarted && !gameEnded`; otherwise `hide()`. Warm-up and
post-match footage stay clean.

`ScoreboardOverlay` gained `hide()` (alpha 0, texture kept so
the next update brings it straight back) and `update` now
re-asserts alpha 1, so "update" implies "show" and visibility
is not a second piece of state to keep in sync.

#### 2. Everything disabled before the whistle

A Kill with no game around it is not a real event, and live
buttons during warm-up invite mis-taps. New `gated(button)`
helper registers a control as game-only; `refreshAllViews`
sets `isEnabled` / `alpha` on all of them from
`gameStarted && !gameEnded`.

Registering rather than gating each button individually means
a new scoring button cannot silently forget the gate. Nine
controls are covered: the six rotation slots, both team
headers' first-serve / -1 / +1, the player-tag buttons, Ball
Served, Replay, Game End, End Set.

`Game Start` is the inverse -- live only when the game is not
running. Stream controls, Export and the pop-up test buttons
are deliberately never gated.

#### 3. No "Serving" pop-up for the opponent

We track the home rotation, so we can name our own server.
The opponent's lineup is not tracked (VME does not either),
which left a bare "Serving" with no name on every one of
their rallies -- noise for no information. `popupSpec` now
returns null unless `servingTeam == Home`.

#### 4. "Entering" instead of "#7 for #7"

A rotation slot that was empty has nobody to swap out, so the
`#OUT -> #IN` subtitle degenerated. When the outgoing jersey
is null the pop-up now reads **Entering** with just the
incoming player, instead of **Sub** with a nonsense arrow.

#### 5. Stop ends the stream, not the match

Removed the "Export match.json now?" prompt from
`onStopStreaming`. Stop is pressed for a battery swap, a
tripod move at the change of ends, or a dropped connection --
the operator hits Start again and keeps going. The match
stays open, scoring keeps working, and Export remains its own
header-bar button. The natural moment for it is after Game
End.

#### 6. Pop-ups slide in and out

Ported `message.py::_x_offset` and its easing: slide in over
the first 10% of the pop-up's life, sit still, slide out over
the last 10%. `1-(1-t)^3` in (decelerating, arrives softly),
`t^3` out (accelerating, leaves briskly).

VME gets this free from its per-frame frame map. Here there
is no render loop to hook, so a `Handler` ticks at 33 ms and
calls `filter.setPosition(x, y)`. Only the position moves --
the bitmap is uploaded once per pop-up, because `setImage`
re-uploads a GL texture and doing that 30x/second for a pure
translation would be waste.

Pinned pop-ups (`holdMs = 0`, the test button) skip the
animation and park at rest -- no slide-out to sit through.

#### 7. Final score card on game_end

New `overlay/FinalScoreOverlay.kt`: centred card showing
`FINAL`, the team names in their own colours flanking the set
count, and every set's score underneath
(`25-21  23-25  25-19`).

Kept as a third filter rather than reusing the pop-up: a
final score wants the middle of the frame, a bigger type
scale and no auto-hide, and making one filter do both would
mean `setScale` changing per event -- which reproducibly
blanks these filters. One more full-screen quad per frame is
cheap by comparison.

Needed per-set history, which nothing tracked. Added
`GameState.setScores: List<Pair<Int, Int>>`, appended in
`onSetEnd` before the running score is wiped and cleared on
`game_start`. Phone-only -- VME derives set results by
replaying the event log, so it is not written to
`match.json`.

#### Also

- Start now calls `pushOverlay()` once the filters are
  attached, so restarting a stream mid-match renders the
  right thing on the first frame instead of waiting for the
  next event.

### Courtside iteration round 10 -- **VME popup port + player photos**

Two changes: the pop-up renderer is now a real port of VME's
`message.py` rather than my own invention, and player profile
pictures are fetched at import and drawn on the pop-ups.

#### Pop-ups now match VME

Read `backend/app/render/overlays/message.py` and the
`overlay_effect` properties on the event classes, and ported
both the look and the event mapping.

**Look** (`overlay/PopupOverlay.kt`):

- **Bottom-right**, clear of the scoreboard bar, was top-centre
- Background is the **team colour**; accent bar 6 px at
  `bg * 0.6` down the left
- Title **bold**, subtitle **regular**, both at
  `videoHeight * 0.020`. Same size on purpose: VME's comment
  says the hierarchy should come from weight, because a title
  40% bigger made action labels shout over the player info the
  viewer actually cares about
- 16 px padding, 4 px line gap, all expressed as fractions of
  frame height so they scale
- Hold 3.0 s, matching `popup_duration_seconds`

**Event mapping** -- previously invented, now ported:

| event | title | subtitle |
|---|---|---|
| `kill` / `ace` / `assist` / `block` / `dive` / `dig` | action label | `#NN Name` |
| `ball_served` | Serving | player at position 1 of serving team |
| `substitution` | Sub | `#OUT Name -> #IN Name` |
| `score_correction` | Score Fix | non-zero deltas |

Corrected from my earlier guesses:

- **`ball_served` pops** -- this is what the operator asked
  for, and it is what VME does.
  `BallServedEvent.overlay_effect_for_state` resolves the
  server from game state (position 1 of the serving team),
  since the event itself carries no player.
- **`highlight` is silent** -- `HighlightEvent.overlay_effect`
  explicitly returns None. It marks a frame for the
  `highlights/` batch render; a pop-up would be noise.
- **`first_serve` is silent** -- it only has a `roster_effect`.
  The serve pops via `ball_served`.
- **`game_start` / `game_end` / `set_end` are silent** -- I had
  added pop-ups for these last round; VME gives them
  `ScoreboardVisibilityEffect` or a score reset and no pop-up.

#### Player photos

VME draws a circle-cropped photo at the left of a player
pop-up. That is now ported end to end.

- **`data/PhotoCache.kt` (new)** -- photos live at
  `filesDir/photos/{teamSlug}/{jersey}.img`. Jersey number is
  the key because that is what VME keys photos by, so renaming
  a player keeps their photo. Extension is generic;
  `BitmapFactory` sniffs the format. `load()` does a
  bounds-only pass first and picks an `inSampleSize`, so a
  12 MP upload does not get fully decoded to fill an 80 px
  circle.
- **`VmeClient.fetchPlayerPhoto`** -- `GET /api/teams/{team}
  /players/{number}/photo`, same Basic auth and timeouts as
  the JSON calls, binary body. Returns null instead of
  throwing: a missing or corrupt photo must never fail an
  import, and the renderer already has a fallback.
- **Fetched at roster import**, not on demand. The pop-up
  renderer runs on the scoring path -- the operator taps
  "Kill" and we draw immediately -- so a gym-Wi-Fi round trip
  there would stall the UI thread. Import is already a network
  step the operator waits on in the warm-up. A re-import
  clears the team's cache first so a departed player does not
  leave their face behind.
- **`Player.profilePicPath` + `Player.localPhotoPath`** -- the
  former round-trips VME's `profile_pic_path` verbatim so the
  editor keeps working; the latter is phone-only and therefore
  serialised as `_local_photo_path`.
- **Rendering** -- cover-crop (never fit: a photo is not a
  logo, and fitting one into a circle leaves a person floating
  in background), crop window biased up 22% because a centred
  square crop of a standing photo lands on the torso, 2 px
  white ring. No photo gets a **jersey-number disc** at
  `bg * 0.55`. A substitution always draws **two** circles even
  when only one player has a photo -- one lone face beside a
  two-player subtitle is exactly the ambiguity VME's comment
  warns about.
- **Decoded bitmaps memoised** for the overlay's lifetime,
  caching nulls too so a corrupt file is not re-read on every
  pop-up (the trap VME's `_avatar_cache` comment calls out).

#### Also

- `Test popup` now uses the first real roster player, so it
  exercises the photo path, and logs how many cached photos
  the roster has.
- Hit the KDoc-nesting trap again: a glob written as
  `events/` + star + `.py` inside a KDoc opens a nested block
  comment and every declaration below it vanishes from the
  class, which surfaced as 49 unrelated "unresolved
  reference" errors in three different files. Left a warning
  in the KDoc itself this time.

#### Unverified assumptions

Both need a look on the device:

1. **Per-pixel alpha.** The pop-up bitmap is cleared fully
   transparent and only the box is painted, so it hugs its
   text like VME's does. The shader
   (`object_fragment.glsl`) is
   `mix(samplerPixel, objectPixel, objectPixel.a * uAlpha)`,
   so `a == 0` should pass the video through. The
   scoreboard's KDoc claims transparency does not work; I
   think that was the recycled-bitmap bug in disguise, since
   a recycled texture reads as opaque black. **If a black
   slab appears around the pop-up, that is wrong** and the
   fix is a fixed opaque box.
2. **`setPosition(x, y)`.** Used the percentage overload
   rather than a `TranslateTo` constant, because the pop-up
   has to clear the scoreboard. An earlier note in this doc
   wrongly said there was no Y-offset API; there is, on
   `BaseObjectFilterRender`.

### Courtside iteration round 9 -- **root cause: ball_served is silent**

Operator clarified: they were watching the **YouTube feed**
(so not missing the pop-ups through the tab problem), and
they had only been testing with the **Ball Served** button.

`ball_served` is in the silent list. `popupTextAndColor`
returns null for it, `firePopup` bails before touching the
overlay. The pop-up path was never exercised. All the GL
investigation in rounds 6-8 was chasing a bug that was not
there.

Lesson, again: confirm the input before debugging the
output. "No pop-up appeared" needed one follow-up question
-- *which button did you press?* -- and it would have been
solved three rounds earlier.

**Fixes**

- **Silent events now log why.** `firePopup` writes
  `popup: ball_served is silent by design` to the on-screen
  log instead of returning quietly. "I tagged X and nothing
  popped" is now self-explaining.
- **Documented the silent list** in the `popupTextAndColor`
  KDoc with the reasoning:
  - `ball_served` -- every rally; a pill strobing across the
    top of the frame is noise, and the scoreboard already
    carries the state
  - `score` / `score_correction` -- the scoreboard numbers
    change in the same frame, a pop-up is redundant
  - `replay`, `cut_start`, `cut_end`, `clip_transition`,
    `focus_in`, `focus_out`, `message`, `assist` -- editor
    metadata, meaningless to a live viewer
- **Widened the pop-up coverage** to everything a viewer
  reacts to:
  - `substitution` -> `SUB - #12 SMITH` (reads
    `player_in_number`, not `player_number`)
  - `game_start` -> `WHITECAPS  vs  RIVERSIDE`
  - `set_end` -> `END OF SET - 2 - 1`
  - `game_end` -> `FINAL - WHITECAPS WINS 3-1`
  plus the existing kill / ace / block / dig / dive /
  highlight / first_serve.
- **`teamName(side)` helper** -- roster team name, falling
  back to the match-level team/opponent string, finally to
  `HOME` / `AWAY`. Replaces the hardcoded "HOME"/"AWAY" in
  the first-serve pop-up, so it now says
  `SERVING - WHITECAPS`.

### Courtside iteration round 8 -- **diagnostic**

Round 7 fixed the layout and the button sizes (operator
confirmed both good). Pop-ups still not visible.

**What was verified by reading the RootEncoder sources**

Went through the whole GL path rather than guessing again:

- `BaseFilterRender.draw()` -- binds the filter's FBO, calls
  `drawFilter()`, then `glDrawArrays(GL_TRIANGLE_STRIP, 0, 4)`.
  Every filter draws a full-screen quad; the sprite only
  changes the *texture coordinates*, not the geometry.
- `MainRender.reOrderFilters()` -- chains filters:
  `filters[i].previousTexId = filters[i-1].texId`. So filter 1
  (pop-up) samples filter 0's output (camera + scoreboard) and
  composites on top. Multiple `ImageObjectFilterRender`
  instances are fine; each has its own program, texture and
  `alpha` field. Pedro confirms multi-filter support in
  issue #1838.
- `GlStreamInterface.draw()` -- drains **one** filter action
  per frame from `filterQueue`, so two `addFilter` calls take
  two frames (~66 ms) to both land. Not a problem.
- `Sprite.getTransformedVertices()` -- hand-computed the
  pop-up case: scale 66x7.5, `TranslateTo.TOP` gives
  `position = (17, 0)`, which produces object texture coords
  running 0..1 across the middle 66% horizontally and the top
  7.5% vertically. **The geometry is correct.**
- Fragment shader passes the sampler pixel straight through
  outside `[0,1]` and `mix()`es inside. Correct.

So the pop-up code is mechanically right as far as the
library sources go. Which means the remaining suspects are
about *observation*, not rendering.

**The likely answer: nobody was looking at the video**

The Events tab fills the screen with the scoring UI. There is
no video on it. The pop-up fires, holds 3.5 s, and hides --
all while the operator is looking at the rotation grid. To
see it you have to be on the Preview tab at the moment it
fires, which is exactly when you are not, because you just
tapped a button on the Events tab.

**Fixes -- make it testable instead of guessable**

- **`show(text, colour, holdMs = 0)` pins the pop-up.** Zero
  means "stay up until the next `show` or an explicit
  `hide`". No auto-hide timer scheduled.
- **"POPUP TEST" section in the MATCH pane** with two
  buttons: `Test popup` pins a red `TEST POPUP` pill on the
  frame, `Clear popup` takes it down. Now the operator can
  pin it, switch to Preview at leisure, and look.
- **"STREAM STARTED" diagnostic is now pinned too** (was
  3.5 s), so it survives a tab switch.
- **Pop-up fires are mirrored into the on-screen log.** Every
  `firePopup` writes either `popup: "KILL - #12 SMITH"` or
  `popup: skipped for kill (not streaming)`. That separates
  "we never created a pop-up" from "we drew one and the GL
  pipeline swallowed it" without needing a laptop.

Next test: Start -> switch to Preview tab -> is `STREAM
STARTED` there? Then `Test popup` -> Preview -> is `TEST
POPUP` there? If both are invisible but the log shows the
fires, the problem is genuinely in the GL pipeline and the
next step is dropping the scoreboard filter temporarily to
see whether a lone pop-up filter renders.

### Courtside iteration round 7 -- **layout fixed**

Round 6 shipped attach-eagerly + `setAlpha` popup toggling,
compact-but-tappable buttons, tighter score header, two-line
position labels. Feedback after installing:

- Top bar STILL expands, specifically when the operator taps
  Start. INVISIBLE tab fix was correct for the previous
  symptom but not this one.
- `-1` and volleyball-emoji first-serve buttons are too narrow
  now -- widen 2x.
- Player pop-ups still do not appear on the video.

**Diagnosis**

- **Header expands on Start.** The status label is a plain
  `TextView` with no single-line constraint. `"idle"` fits
  the row height, but the moment we call `setStatus(
  "contacting YouTube")` or `"LIVE - streaming"` the label
  wraps to two lines and drags the header bar taller (bar
  height is `WRAP_CONTENT`, so it follows the tallest child).
- **Pop-ups.** Traced the RootEncoder pipeline:
  - `com/pedro/encoder/input/gl/render/filters/object/BaseObjectFilterRender.java`
    -- shader is `mix(samplerPixel, objectPixel, objectPixel.a
    * uAlpha)`. Per-pixel alpha IS honoured; earlier claim
    that transparent pixels come out black was probably a
    symptom of a different bug (bitmap allocated as ARGB but
    with un-initialised pixels drawn on top). Fully-opaque
    bitmap with `setHasAlpha(false)` + `drawColor(SRC)` is
    the right way.
  - `ImageObjectFilterRender.drawFilter()` explicitly forces
    `uAlpha = 0` when `streamObjectTextureId[0] == -1`, i.e.
    when no image has ever been loaded. So calling
    `setAlpha(0f)` in `attach()` before any `setImage()` is a
    no-op; the filter is naturally hidden until first show.
  - `Sprite.translate(TranslateTo.TOP)` calls
    `position.x = 50f - scale.x / 2f`. `setScale` MUST come
    before `setPosition(TranslateTo.*)` or the horizontal
    centring is wrong. My code already does this in the
    right order.

  So the code is *mechanically* correct. The most likely
  reason the operator sees no pop-up: the six player-tagged
  events (Kill/Ace/Block/Dig/Dive/Highlight) plus first-serve
  did not fire during the last test -- the flow they exercised
  was Start -> +1, and a Score is silent in the pop-up path.

**Fixes**

- **`isSingleLine = true` + `ellipsize = END` on status
  label.** Also on the Events/Preview tab buttons for the
  same reason.
- **Header bar pinned to 56dp.** Even if a child View somehow
  wraps, the bar itself cannot grow past this fixed height.
- **`compactButton` horizontal padding doubled
  (18px -> 36px).** `-1` and volleyball-emoji first-serve
  now have roughly twice the tap width.
- **`PopupOverlay.attach()` no longer calls `setAlpha(0f)`.**
  Redundant -- the shader hides it until the first `setImage`
  regardless. Kept the KDoc note about `setScale` needing to
  come before `setPosition(TranslateTo.TOP)` so nobody re-
  orders those lines later.
- **Diagnostic: fire a "STREAM STARTED" pop-up on Start.**
  If the operator sees this pop-up flash across the top of
  the frame on Start, the pop-up filter is working and the
  question is only which event handlers do or do not fire
  `firePopup`. If it does not appear even once, the filter
  itself is broken and we look at pipeline order, GL thread
  timing, etc.

Next step: install the APK, hit Start, watch for the pop-up
on the phone screen. Report back with `adb logcat -s VMEPopup:I`
regardless of what shows on-screen.

### Courtside iteration round 6 -- **fixed**

Round 5 shipped `compactButton`, INVISIBLE tabs, on-court sub
filter, spanned position labels and the pop-up overlay. Feedback
from install-and-test:

- The compact buttons went too far -- they read as tiny (12sp
  text, no min height, 6px vertical padding got interpreted as
  ~24dp buttons). Courtside operators need a tap target.
- The top bar was still growing after a Preview -> Events
  switch. INVISIBLE fixed the *pane* re-measure, but the score
  header row itself was 48dp+padding tall because the Undo
  button pinned it to Button's default `minHeight`.
- Player pop-ups never appeared on the video. Lazy
  `addFilter` on first `show()` -- called after `startStream`
  had already been running -- silently dropped.
- The three-line position labels (P4 / #12 / Smith) were
  wider *and* squatter than useful; a single "#12 Smith" line
  reads just as well.

**Fixes**

- **`compactButton` re-tuned.** Kept `minWidth = 0` /
  `minimumWidth = 0` so a two-character label does not eat
  88dp of the team-header row, but dropped the `minHeight = 0`
  override so the default 48dp min height is preserved --
  buttons stay tap-friendly. `textSize` is back to the Button
  default (14sp -- 12sp made them unreadable at courtside
  distance). Padding tightened to `18,8,18,8`.
- **Score header height cut.** Big score font `40sp -> 32sp`,
  row padding `16,12,16,12 -> 12,4,12,4`,
  `includeFontPadding = false` on the score TextViews so the
  built-in ascent/descent slack does not add ~10dp on top and
  bottom. Undo button routed through `compactButton` so its
  height matches the rest of the row rather than the 48dp
  default. Net: the score header takes roughly half the
  vertical space it did.
- **Pop-up overlay: attach eagerly, toggle with alpha.**
  `PopupOverlay` restructured so its filter is added to the GL
  pipeline **once**, alongside the scoreboard, before
  `startStream`. `attach()` sets scale (`66x7.5%`) + position
  (`TranslateTo.TOP`) + `setAlpha(0f)` so it starts hidden.
  `show(text, colour)` renders a fresh opaque bitmap, calls
  `filter.setImage(bmp)` then `filter.setAlpha(1f)`, schedules
  a hide after 3.5 s. `hide()` just calls `setAlpha(0f)` --
  no more `removeFilter`, which was fragile on a live stream.

  `BaseObjectFilterRender.setAlpha` drives the shader's
  `uAlpha` uniform; at 0 the whole filter region blends fully
  transparent and the video comes through unmodified. Cheap
  and race-free compared to add/remove-on-a-running-pipeline.

  Added `Log.i("VMEPopup", ...)` at attach / show / hide so
  next time the overlay silently vanishes we have the tag to
  grep in logcat.

  On stop, `stream.getGlInterface().clearFilters()` is called
  belt-and-braces before the next start rebuilds the overlays.

- **Two-line position label.** `positionLabel(pos, jersey)`
  now returns a plain `"P{n}\n#{jersey} {shortName}"` string,
  no `SpannableString` needed. Empty slot stays `"P{n}\n?"`,
  so an incoming sub does not grow the grid. Button `textSize`
  bumped to 12sp with `6,8,6,8` padding -- a bit more legible
  than the 10sp three-line variant.

### Courtside iteration round 5 -- **fixed**

Feedback after installing the fresh-per-update overlay APK and
tagging a real rally:

- The spelled-out `1st serve` and `-1` buttons are too wide on
  portrait -- room-limiting the +1 button.
- Long team names wrap mid-word in the score header and shift
  everything vertically. Ugly.
- Switching Preview -> Events "spreads the layout out\n  vertically" -- row heights change on the way back.
- The jersey picker on a substitution shows players already on
  court; picking one of them is a no-op and confusing.
- Position slots only show `#12`; the operator would rather see
  the short name too ("they've all played 5+ years, they know
  the roster by name faster than by number").
- No player pop-ups on the video. The scoreboard updates score
  and set counts, but a Kill or an Ace is not visible to viewers.

**Fixes**

- **Compact team-header buttons.** New `compactButton(label,
  onClick)` helper: zeroed `minWidth` / `minimumWidth` /
  `minHeight` / `minimumHeight`, `textSize = 12sp`, tighter
  padding. Used for both the volleyball-emoji first-serve
  button and the `-1` button. `+1` stays a full-size accented
  button because it is the highest-frequency tap.
- **Team names never wrap mid-word.** `isSingleLine = true` +
  `ellipsize = END` on both the score header team-name labels
  and the team-card header-row name. Long club names truncate
  with `...` instead of wrapping to a second line and pushing
  the row height around.
- **Tab switch uses `INVISIBLE`, not `GONE`.** `GONE` was
  making the events pane re-flow taller on the way back from
  Preview because measurement pass constraints differ when
  only one child of the FrameLayout participates in layout.
  `INVISIBLE` keeps both children in the layout pass; only
  paint changes. Bonus: the `SurfaceView`'s hardware surface
  survives the tab switch instead of being torn down and
  re-created every time, so the camera preview does not
  restart on every Events <-> Preview tap.
- **Jersey picker filters on-court players in the sub flow.**
  `pickPlayer` gained an `excludeOnCourt: Boolean` parameter.
  `subForPosition` calls it with `true` -- current
  `gameState.homePositions.values.filterNotNull()` are
  filtered out of the list. Player-tag buttons
  (Kill/Ace/Dig/etc.) still see the full roster, because a
  bench player *can* be credited retroactively for something
  from earlier in the rally.
- **Position labels show short names.** New
  `positionLabel(pos, jersey)` builds a three-line
  `SpannableString`: `P{n}` on top, `#{jersey}` in the middle,
  and the player's short name underneath at `RelativeSizeSpan(
  0.78)` -- one text view, no per-cell view tree. Empty slots
  render `P{n} / ?` (two lines only) so the grid does not
  visibly grow when a subber comes in. Position button text
  size dropped to 10sp with tighter padding so the 3x2 grid
  still fits above the player-tag buttons on portrait.
- **`overlay/PopupOverlay.kt` -- new.** Transient event pop-up
  burned into the RTMP feed, matching the spirit of
  `backend/app/render/overlays/message.py`. Horizontal pill
  at `TOP` (no `TOP_CENTER` in `TranslateTo`; `TOP` is
  horizontally centred), ~66% x 7.5% of the video. Fully
  opaque -- same reason the scoreboard is opaque, per-pixel
  alpha is not honoured by `ImageObjectFilterRender`. Dark
  team-accent background with a solid team-colour band on
  the left, white bold centred text like `KILL - #12 SMITH`
  or `SERVING - HOME`. Held for 3.5 s, then
  `stream.getGlInterface().removeFilter(...)` detaches it
  (there is no "clear this filter" API and a transparent
  bitmap does not work; add-remove is the workable pattern).
  A second `show()` within the hold window cancels the pending
  hide and pushes the new label immediately, so rapid taps do
  not stack pop-ups.

  `MatchLiveActivity.firePopup(type, payload)` is called from
  `record()` alongside the scoreboard update. Silent on
  lifecycle events (game start / set end / ball served /
  replay / substitution / score / score correction / undo);
  fires for the six player-tagged events plus `FirstServe`.
  Wrapped in `runCatching` like `pushOverlay`, so an overlay
  failure never takes the scoring surface down.

### Courtside iteration round 4 -- **fixed**

Round 3 shipped the two-buffer overlay swap, the `1st serve`
button on each team header, Undo, and auto-preview. The two-buffer
swap turned out to crash the app on the second overlay update
(Ball Served then +1 -> hard crash), and the spelled-out
`1st serve` label ate too much of the team row on portrait phones.

**Fixes**

- **Overlay: fresh bitmap per `update()`.** A real device stack
  trace nailed the actual cause -- not "GL still reading buffer
  A" as I first guessed, but:

  ```
  java.lang.RuntimeException: Canvas: trying to use a recycled
    bitmap android.graphics.Bitmap@b9b9b59
    at android.graphics.Canvas.<init>(Canvas.java:120)
    at ScoreboardOverlay.update(ScoreboardOverlay.kt:194)
    at MatchLiveActivity.record(MatchLiveActivity.kt:686)
    at MatchLiveActivity.doScore(MatchLiveActivity.kt:644)
  ```

  `ImageObjectFilterRender.setImage(bmp)` calls `bmp.recycle()`
  after it has uploaded the pixels to GL. Every keep-a-bitmap
  pattern (redraw-in-place, two-buffer swap, N-buffer pool) will
  therefore try to `new Canvas(bmp)` on an already-recycled
  bitmap and crash on the second score.

  This *also* explains the original "scoreboard goes black on
  first score" symptom before the two-buffer attempt: the first
  `setImage` recycled the shared bitmap, the follow-up
  `Canvas.drawXxx` calls silently no-op'd against the recycled
  backing, and the GL side kept drawing whatever empty state
  was left after recycle. The identity-check hypothesis was
  wrong -- it's not identity, it's recycle.

  Fix is single-use: allocate a fresh
  `Bitmap.createBitmap(barW, barH)` on every `update()`, hand it
  to `setImage`, and drop our reference. RootEncoder owns it
  from that point on and recycles it when done. At ~250 KB per
  allocation and one allocation per event this stays well under
  a megabyte per set even in a rally-heavy match.

  KDoc on `ScoreboardOverlay` updated in-line to include the
  actual stack trace so the next person who reads it doesn't
  re-invent the two-buffer swap and re-trip the same crash.
  Two-buffer swap and `back`/`front` fields removed entirely.
  `pushOverlay()` wrapper stays as belt-and-braces so any
  *future* overlay bug does not take the scoring surface down.

  (Root-cause debugging note: logcat from the device is the
  fastest path. The plan-doc "Verification owed on hardware"
  section asks for `adb logcat -s AndroidRuntime` alongside the
  screenshots for exactly this reason -- three iterations of
  guessing beat one iteration with a stack trace.)
- **Volleyball emoji for the first-serve button.** `1st serve`
  spelled out was pushing the `+1` button off-screen on portrait
  phones. Replaced with the volleyball emoji (`\uD83C\uDFD0`
  = U+1F3D0 "volleyball") at 16sp -- self-describing in the same
  visual space as a `+1`.
- **`pushOverlay()` wrapper.** Both call sites (`record()` and
  `onUndo()`) now go through a `pushOverlay()` helper that wraps
  `overlay.update` in `runCatching`. The event is already
  persisted before the overlay runs, so an overlay error should
  never take the scoring surface down with it. Any failure logs
  to the on-device log with class name + message, so the next
  courtside bug report has diagnostic weight even without a
  laptop attached.

### Courtside iteration round 3 -- **fixed**

Round 2 shipped an opaque VME-style bar, greyed the Ball Served
button, and removed Cut/Sub. The blackbox-on-score bug came right
back though: my "call `setScale` once" hypothesis was wrong (or at
least incomplete). Also from this round of feedback:

- The "First serve H / A" pair in the MATCH section reads like a
  puzzle -- courtside operators want the action next to the team
  it applies to.
- No mis-tap recovery except deleting and re-editing JSON on a
  laptop. Every tap deserves an Undo.
- Camera preview only comes up after Start. The operator has no
  way to frame the shot or confirm the camera works before going
  live.

**Fixes**

- **Overlay: two-buffer swap.** `ImageObjectFilterRender.setImage`
  called with the same `Bitmap` instance twice in a row does not
  re-upload the texture on the S24 -- probably an identity check
  in `ImageStreamObject.load`. Redrawing the single bitmap in
  place therefore leaves the GPU showing the *first* frame we
  ever uploaded, and after the first score our redraw diverges
  from it (different score, ball-served flag, points), which
  shows up as the whole overlay going black.

  `ScoreboardOverlay` now keeps two same-size bitmaps (`back` /
  `front`) and alternates on every `update()`: draw into back,
  `setImage(back)`, swap. The filter always sees a bitmap
  reference it has not seen since its last upload, so the
  identity check falls through and the texture actually reloads.

  Bar dimensions still computed once at construction; `setScale`
  and `setPosition` still called once at `attach()`. That code
  was never the bug -- but it was also never the *right* thing
  to call per-update, so it stays as is.

- **`1st serve` button on each team header.** Removed the two
  "First serve H / A" buttons from MATCH. Each team card's name
  row now has a small `1st serve` button which records
  `FirstServe` with that team's wire value. The action is
  self-describing where it lives now, no legend needed.

- **Undo button between the two big scores.** New
  `undoButton` in the score header. `onUndo()` drops the last
  event, re-persists, then re-derives state from the trimmed
  event list -- cheaper than inverting per-event and by
  construction it leaves state byte-identical to "before the
  last tap". Disabled + dimmed when the event list is empty.
  Logs the dropped event so the audit trail still shows it.

- **Auto-preview.** Added `ensurePrepared()` -- calls
  `prepareVideo` / `prepareAudio` at most once per activity
  lifetime, guarded by a `prepared` flag (RootEncoder does not
  want a second prepare call).

  `surfaceCreated` now calls `ensurePrepared()` and
  `attachPreview()` unconditionally: the camera lights up as
  soon as the operator switches to the Preview tab, without
  Start ever having been pressed. `attachPreview()` is now
  idempotent (early-returns when `stream.isOnPreview`) because
  it is reached from both the surface callback and the Start
  path. `onStartStreaming` also just calls `ensurePrepared()` --
  when Start fires after auto-preview, prepare is a no-op and
  the flow drops straight to YouTube setup.

### Courtside iteration round 2 (feedback after the opaque bar) -- **fixed**

On the first opaque-bar rehearsal, the bar rendered correctly at
startup and then went black the instant we tapped `+1`. Also from
the same rehearsal:

- Cut Start / Cut End are noise -- clip cutting is editor work,
  not something the operator does live.
- Ball Served has no state feedback -- the operator cannot tell
  "was this rally marked served yet?" without scrolling the log.
- The Start button emits one-shot log lines, but the header just
  says `stopped` -> `live` with nothing in between; if any step
  hangs, there's no way to see where.
- The Sub button is redundant now that the position grid opens
  the jersey picker directly.

**Fixes**

- `ScoreboardOverlay.kt`: bar dimensions and bitmap are computed
  **once** at construction using the widest plausible score
  (`"99  -  99"`, two-digit set counts) and the actual team names.
  `attach()` calls `setScale`/`setPosition` **once** and never
  again; `update()` only redraws the fixed-size bitmap and pushes
  it via `setImage`. That was the actual blackbox trigger:
  `ImageObjectFilterRender.setScale()` called after the filter is
  running reproducibly wipes the overlay black on this device.
- `MatchLiveActivity.buildMatchSection`: dropped the Cut Start /
  Cut End row entirely. The buttons are the *only* callers of
  `EventType.CutStart` / `EventType.CutEnd` on this device, but
  the enum values stay: another activity or a future re-import
  might still emit them, and dropping them would break the
  round-trip promise with VME's `EVENT_TYPES`.
- `MatchLiveActivity.buildTeamHeaderRow`: dropped the "Sub"
  button; only the `-1` and `+1` remain on each team's row. The
  6-slot rotation grid taps still open the jersey picker for that
  position (unchanged), which is one tap fewer than the old
  Sub-then-position-then-jersey flow.
- `MatchLiveActivity`: added a `ballServedButton` field and, in
  `refreshAllViews()`, greyed it out and switched its label to
  `"Ball Served \u2713"` while `gameState.ballServedSinceLastScore
  == true`. The engine already clears that flag on Score (and
  keeps it clear on Replay), so the button comes back live for
  the next rally without any extra wiring.
- Streaming status is now step-by-step: `preparing encoder ->
  encoder ready -> contacting YouTube -> listing YouTube streams ->
  creating broadcast -> binding broadcast -> connecting to RTMP ->
  handshaking with RTMP -> LIVE - streaming`. On any failure the
  label carries the failing phase (`YouTube setup failed`,
  `connection failed`, `camera failed`, `RTMP auth error`) instead
  of just staying on `stopped`. A tiny `setStatus(text)` helper
  makes the calls thread-safe.

### Scoreboard rendered as a black rectangle over the video -- **fixed**

The first on-air test showed the overlay region as a solid black
box in the top-left of the YouTube stream: no team names, no score,
no rounded corners, just the exact rectangle the filter was told to
render into. The bitmap contents were completely invisible.

**Root cause**

`ImageObjectFilterRender` uploads the ARGB_8888 bitmap to a GL
texture, but on the S24 the fragment shader effectively ignores the
per-pixel alpha channel and writes `gl_FragColor = vec4(rgb, 1)` --
transparent pixels come through as opaque black. `setAlpha()` exists
on `BaseObjectFilterRender` but is region-wide, not per-pixel. There
is no VME-Python-style CPU alpha composite available here; the whole
overlay lands on the video via a GL shader we don't own.

And the earlier `#CC000000` semi-transparent backgrounds compounded
it: even where I *thought* the overlay was intentionally translucent,
the result was fully opaque black.

**The fix: fully opaque bar, exactly like VME's broadcast style**

`overlay/ScoreboardOverlay.kt` rewritten as a port of
`backend/app/render/overlays/scoreboard.py`:

- Five sections, left-to-right, with slanted broadcast-style
  dividers: `home_color | dark | score | dark | away_color`.
- Section colours match the Python reference: `#503C64` for the
  sets sections and `#2D234B` for the centre/point strip -- opaque
  RGB, no alpha.
- Font sizing math ported verbatim: `main_h = video_height * 0.05`,
  team text at `0.45 * main_h`, score at `0.50 * main_h`, sets at
  `0.40 * main_h`.
- Slanted dividers drawn as five `Path` polygons per
  `_draw_slanted_blocks`; no supersampling pass yet -- Android's
  antialiased path fill is smoother than Pillow's polygon, and the
  Python version needs supersampling *because* Pillow does not
  antialias polygons. If a jagged diagonal shows up on a real feed,
  we add a 4x scratch bitmap here too.
- Point-history strip below the main bar: same `SECTION_CENTER`
  colour, one dot per point (`homeColor` for us, `awayColor` for
  them), sized off `max(15, len(points))` slots so a rally-heavy
  set does not run out of room. Requires the new
  `GameState.pointHistory` field.
- Positioned via `TranslateTo.BOTTOM` (which is horizontally
  centred; there is no `BOTTOM_CENTER` value in the enum -- only
  the four corners plus edges). Y-offset from the frame bottom is
  not exposed by `setPosition`, so we accept flush-bottom instead
  of VME's `y = h - oh - 30`. Close enough for a live stream.
- Bitmap is sized to the exact bar (`barW x barH`), not to the
  video frame. `setHasAlpha(false)` on the bitmap because every
  pixel is drawn opaque; `drawColor(SECTION_CENTER, SRC)` clears
  it opaque at the start of each redraw.

**State-engine change to feed the strip**

`GameState.pointHistory: List<Boolean>` -- true when home won that
rally. Appended in `onScore` (the Score/Kill/Ace path), cleared in
`onSetEnd`. Not persisted separately: the fold over the events
list reproduces it on every state read.

**Not ported (deliberately)**

- **Team logos.** VME's Python renderer fetches, decodes, circle-
  masks and cover-crops each logo into a per-diameter cache.
  Doing the same here needs a fetch layer against
  `/logos/{team}.png`, a disk cache under `filesDir/logos/`, and
  the same crop/mask/paste code. Not a v1 blocker; team-coloured
  blocks with names are legible on their own. Recorded as v2.
- **Overlay opacity < 1.0.** VME uses 0.9 by alpha-compositing on
  the CPU before the encoder sees a frame. We cannot -- see root
  cause above -- so the Android overlay is 1.0. Visually louder
  than the Python version, but a courtside operator prefers
  legibility over subtlety.
- **Message overlay.** Ported the scoreboard only; the message
  overlay in `backend/app/render/overlays/message.py` is not on
  the courtside phone's job list.

### Content overlapped the status bar / clock -- **fixed**

Android 15 turns edge-to-edge on by default. Programmatic layouts
do not get automatic padding for the status bar, so the header of
every activity was hidden behind the system clock.

**What shipped**

- `ui/Insets.kt` -- `View.applySystemBarInsets()` extension that
  installs a `ViewCompat.setOnApplyWindowInsetsListener` and pads
  the root view by `systemBars() or displayCutout()`. Returns
  `WindowInsetsCompat.CONSUMED` so children (SurfaceView, in the
  match screen) do not re-apply the same padding.
- Called from `HomeActivity`, `RosterImportActivity`,
  `MatchSetupActivity`, `MatchLiveActivity`, and `MainActivity`
  (the spike), right after each `setContentView`.
- No manifest theme change: the fix is per-activity, and any theme
  attempt to opt out of edge-to-edge is deprecated in
  targetSdk >= 35 anyway.
