# Android streaming app — spike plan

Status: **step 0 harness built, not yet run on hardware.**
The harness is `android/` - see [its README](../android/README.md).
Everything past step 1 is still parked.

Goal: replace the current two-app match-day chain with one Android app
that captures the DSLR over HDMI→USB, burns in the VME scoreboard, and
pushes RTMPS straight to YouTube.

```
today:   DSLR --HDMI--> USB adapter --> [USB Camera Pro] --RTMPS--> [SidelineHD] --> YouTube
                                                                     (adds overlay)

target:  DSLR --HDMI--> USB adapter --> [ VME Streamer ] --RTMPS--------------------> YouTube
                                                (adds overlay)
```

The rig itself is solved and in service: powerbank, 5G router, HDMI→USB
adapter, DSLR. Nothing below questions the rig.

---

## Scope decisions already made

- **No live scoring in this app.** The editor is a webapp and is not
  suited to courtside scoring. The streamer is an *encoder*, not a
  scorebook. Tagging stays a post-match activity in the existing editor
  unless and until that changes.
  → This deletes the "app is also the tagger" design and everything
  that followed from it (local WebSocket to a scoring device,
  offline event queue, auto-generated description timestamps).
  Those ideas are recorded in "Deferred ideas" below, not in the plan.
- **No video preview needed.** The DSLR's own screen is the viewfinder.
  The app can be effectively headless: a status screen (connected /
  bitrate / dropped frames / elapsed), not a monitor. This is a real
  simplification — `startPreview` is optional, and skipping it saves a
  GL surface and some heat.
- **Scoreboard state has to come from somewhere.** With no live scoring
  in-app, the overlay needs an input. Options, cheapest first:
  1. A minimal in-app score widget (+1 home / +1 away / set over).
     Not scoring — four buttons. Someone taps the phone between rallies.
  2. The phone polls the VME backend for a "current score" that
     somebody updates from the webapp on another device.
  3. No scoreboard at all in v1; stream clean video.
  Decide this before step 3; it changes what the app is.

  Option 1 is now the clear favourite, because those taps are also the
  export (see next section). The widget pays for itself twice: it
  drives the overlay *and* it produces the match record.

- **VME integration is an export/import file, not a live API.** The app
  writes a match record; VME imports it as a match with events already
  in place. No network dependency in the gym, no OAuth on the phone, no
  partial-sync states to reason about, and the app stays useful when
  the backend is unreachable — which is most of the time in a gym.

---

## Step 0 — UVC capture spike (do this first, one evening)

**This is the one genuinely uncertain thing.** Everything else is
well-trodden. Do not write app code until this passes.

RootEncoder's `extra-sources` module provides `CameraUvcSource`, which
wraps [`shiyinghan/UVCAndroid`](https://github.com/shiyinghan/UVCAndroid).
That wrapper — not RootEncoder — is where HDMI capture cards fail:

- RootEncoder discussion **#2006**: HDMI→USB card fails with
  `could not negotiate with camera: err = -51`; enumerates
  1920x1080@60 MJPEG, then `setPreviewSize` throws
  `IllegalArgumentException: Failed to set preview size`. Same card
  works on PC and iPad. Maintainer: it's the UVC library.
- RootEncoder issue **#1644**: `CameraUvcSource` works on Android 12,
  black/stuck preview on Android 14, across several devices and
  cameras. Cross-referenced to UVCAndroid issue #83.

**USB Camera Pro working on this rig does not predict UVCAndroid
working on this rig.** Different libuvc build, different format
negotiation.

### Procedure

Build `android/` and run it on the actual phone with the actual adapter:

```
powershell -NoProfile -ExecutionPolicy Bypass -File android\scripts\bootstrap.ps1   REM once
android\deploy.cmd
```

The bootstrap installs a private JDK + Android SDK under
`%LOCALAPPDATA%\vme-android` with no admin rights and no machine-wide
changes. **Pair the phone over wireless debugging**, not USB - the HDMI
adapter takes the only port, so a cable and a capture test cannot happen
at the same time.

It replaces the "run two demo apps" plan with one harness whose buttons
map onto the questions:

1. **1 PROBE** - reads the USB descriptors directly, with no library in
   the way, and prints every advertised format with its real frame
   rates. It also says whether a USB **audio** interface exists at all.
   Paste the output into "Findings".
2. **2 PREVIEW** - opens the adapter through UVCAndroid and renders it.
   This is the exit criterion below.
3. **3 STREAM** - RTMPS, which is really step 2, but it is two more
   lines once the camera is open.

The probe comes first on purpose. When libuvc fails with
`could not negotiate with camera: err = -51`, that message cannot tell
you whether the adapter lacks the format or the library is mis-asking -
and those have completely different fixes. The descriptors can.

**One thing found while building the harness, before any hardware:**
RootEncoder's `CameraUvcSource` calls bare `openCamera()`, and its
`create()` *ignores* the width/height/fps passed to `prepareVideo` -
those configure the encoder, not the camera. So the stock source streams
whatever the adapter defaulted to, upscaled, with no way to ask for
anything else. The harness ships its own `UvcVideoSource` that opens
once to read `supportedSizeList`, then reopens with a chosen entry via
`openCamera(Size)`, and never calls `setPreviewSize` - which per the
UVCAndroid author (issue #1) is the wrong tool for choosing a format up
front, and per issue #135 is what throws `Failed to set preview size`
when a card advertises 1080p at 60fps only and you ask for 30.

### Also in the same evening: audio

HDMI capture audio arrives as a **separate USB audio (UAC) device**, not
inside the UVC video stream. RootEncoder's `MicrophoneSource` will not
pick it up by default.

The harness handles both halves of this: **1 PROBE** reports whether the
adapter exposes an audio interface at all, and **2 PREVIEW** calls
`MicrophoneSource.setPreferredDevice()` with the `TYPE_USB_DEVICE` entry
if one exists, logging what it settled on. No custom `AudioSource`
needed - the routing hook is already there and returns a `Boolean`.

If there is no USB audio device, the phone mic is used and the log says
so. For a gym that is arguably the better outcome anyway: crowd noise
beats a line feed of nothing.

### What to record

- Adapter chipset (MS2109-class = USB 2.0, typically 1080p30 MJPEG only,
  1080p5 uncompressed; MS2130/USB3-class = easier).
  **Measured: USB 2.0 class behaviour exactly — 1080p MJPEG to 50fps,
  1080p uncompressed only 10/5fps.**
- Android version of the test device.
- Whether preview worked, at what format/resolution/fps.
- Whether USB audio enumerated and captured.

### Exit criteria

A live UVC preview at 1080p30 from the HDMI adapter, plus a decision on
where audio comes from. If this fails on every path, stop — the
alternatives (direct libuvc JNI, different adapter, or keeping capture
in a separate app and consuming it over local RTSP) are a different
project with a different budget.

---

## Step 1 — dropout test on the CURRENT setup (20 minutes, no code)

Today SidelineHD acts as a **relay**: their server absorbs the rig's
network jitter and YouTube sees a clean feed from a datacenter. Pushing
direct means *a 5G dropout is YouTube's dropout*.

YouTube has no documented `reconnect_window` (Mux does; YouTube
doesn't). The grace period is observed behaviour, roughly a minute
before the broadcast ends and the archive closes.

Test, on a throwaway unlisted broadcast:

1. Set `contentDetails.enableAutoStop = false` **explicitly**. With
   autoStop on, a brief dropout *ends the match*.
2. Stream for a few minutes. Kill the router for **30 s**. Reconnect to
   the same stream key.
3. Repeat with **3 min**.

Record: did the broadcast survive, did the archive stay one video, how
long until the encoder recovered on its own.

**This decides whether direct-to-YouTube is viable at all.** If a 30 s
outage kills the stream and outages happen, the honest answer may be to
keep a relay (self-hosted nginx-rtmp on a VPS, or stay on SidelineHD)
and only replace the capture app.

---

## Step 1b — the export/import bridge (independent of everything above)

This can be built and shipped **before, after, or entirely without** the
streaming app, because it's a file format plus an importer. It is also
the part with the clearest payoff per hour spent.

### What it prepopulates, honestly

Nobody taps `kill` / `dig` / `block` while operating a camera. What a
courtside operator *can* realistically produce is the **scoreboard
spine**:

- `score` (and `score_correction`)
- `set_end`, `game_start`, `game_end`
- `first_serve` / `ball_served` if the widget makes it one tap
- `substitution` and timeouts if they're entered at all

That's precisely the tedious half in VME — score bookkeeping is the
part that has to be right and is no fun to reconstruct — while the
action tags (`kill`, `ace`, `dig`, `highlight`, focus spans) stay a
deliberate post-match pass. Being clear about this split is what keeps
the feature honest: it removes drudgery, it does not remove tagging.

It also gives rally boundaries for free, which is what `condensed` and
the serve-gap warnings are built on.

### Time base — solvable, with one thing to verify

The export carries **wall-clock** timestamps. VME stores events as
`clip_id` + `local_frame`. Bridging them needs an anchor:

1. `Clip.start_recording_time` — VME already ffprobes `creation_time`
   per clip for the auto-cuts heuristic.
2. A stored per-match `import_offset_seconds`, adjustable by hand.

**The DSLR clock is now synced to the phone, so (1) is the primary path
and the mapping is arithmetic.** (2) becomes a correction affordance
rather than a required step — build the offset field into the schema
from day one even if the UI for it comes later, because retrofitting a
per-match offset after events are already placed is the annoying
version of this problem.

Still worth knowing before trusting the arithmetic:

- **Verify `creation_time` means what we think once.** ffprobe's
  `creation_time` is the *start* of recording on most cameras, but some
  write the file-close time instead — which would put the anchor one
  clip-length out, consistently, and look like a large fixed offset.
  VME's existing fallback (`mtime - duration`) exists precisely because
  this metadata is unreliable. One clapper test settles it: tap a score
  event at a visually identifiable instant, import, check where it
  lands.
- **Timezone and DST.** Store UTC in the export, convert once at
  import. `creation_time` is UTC; camera-local metadata is not always.
- **Human tap latency is real and is NOT clock error.** The operator
  taps a second or two *after* the ball lands. That's a systematic late
  bias on every score event, and it's the kind of thing the offset field
  can absorb — but only if it's understood as a separate correction
  from clock skew, since a future live-scoring device would have a
  different bias.
- **Re-check after a battery pull.** DSLRs commonly lose the clock when
  the battery is out long enough. The importer's dry run is the place
  this gets caught, which is another reason it defaults on.

Also note the stream and the card are *different recordings*: the
stream starts before the first whistle and runs continuously, while the
card may be split into several clips with gaps. The offset is to the
global timeline, and clip gaps still have to be resolved through the
existing clip/frame machinery.

### Format sketch

A single JSON file, using the **existing event type names** so the
importer is thin:

```jsonc
{
  "source": "vme-streamer/1.0",
  "broadcast": { "video_id": "...", "started_at": "2026-09-10T18:31:22Z" },
  "match": { "opponent": "Bellevue", "date": "2026-09-10" },
  "events": [
    { "type": "game_start", "at": "2026-09-10T18:33:04.120Z" },
    { "type": "score", "at": "2026-09-10T18:33:41.880Z", "team": "home" }
  ]
}
```

### Importer

`POST .../matches/{match}/import` with:

- **A dry run that is the default.** Report how many events of each
  type, the wall-clock span, and where the first and last would land on
  the current timeline — *before* touching `match.json`.
- **Merge vs replace**, stated explicitly. Re-importing after fixing the
  offset must not double every score.
- Provenance on each imported event, so a later "undo the import" is
  possible without guessing which events were hand-made.

### Bonus: it may remove the backend from step 2 entirely

If the only VME integration is a file, the app doesn't need to talk to
the VME API at all in v1 — the stream key can be fetched once from
YouTube Studio (or shown by the webapp and scanned as a QR) and stored.
That deletes `POST /api/live/sessions`, the OAuth brokering, and every
auth failure mode from the critical path of a match day.

The backend-brokered version below is still the better end state (title,
description, thumbnail and privacy all come from templates you already
maintain), but it is now an **improvement, not a prerequisite**.

---

## Step 2 — UVC → RTMPS → YouTube, no overlay

Smallest thing that replaces both apps.

**Backend** (`backend/app/api/live.py`, new) — *optional, see step 1b;
a hardcoded stream key is a legitimate v1*:

- `POST /api/live/sessions` → creates the broadcast, returns ingest URL
  + stream key + `video_url`.
- Reuse a single `liveStream` resource forever
  (`contentDetails.isReusable` defaults to `true`) — one fixed key, one
  fewer call per match.
- `contentDetails.enableAutoStart = true`, `enableAutoStop = false`.
  AutoStart removes every `liveBroadcasts.transition` call and its poll
  loop; autoStop off is the dropout protection from step 1.
- `latencyPreference: LATENCY_NORMAL` — the largest buffer, which is
  what a jittery 5G link wants. Ultra-low latency is *worse* under
  jitter and nobody watching a school volleyball match needs 2 seconds.
- Title/description/thumbnail from the **existing** template and
  thumbnail code (`app/upload/templates.py`, `app/render/thumbnail.py`).
- `status.privacyStatus` from team settings, same as uploads.

**Why the backend and not the app:** the OAuth refresh token stays on
the server, the quota ledger stays the single accountant, and the phone
never holds a Google credential. The app gets an ingest URL and a key —
nothing that's worth stealing for longer than one match.

**App:**

```kotlin
GenericStream(context, connectChecker, CameraUvcSource(), usbAudioSource)
  .apply {
    prepareVideo(1920, 1080, 6_000_000, fps = 30)
    prepareAudio(48_000, true, 128_000)
    startStream("$ingestUrl/$streamKey")
  }
```

1080p**30**, not 60 — sustained encode plus radio for two hours on a
phone is a thermal problem before it's a bandwidth one. H.264, not
HEVC: YouTube transcodes anyway and H.264 ingest is the boring,
universally-supported path.

Plus: `ConnectChecker` → auto-reconnect, `setVideoBitrateOnFly` →
degrade to ~3 Mbps rather than drop frames, and a foreground Service so
the OS doesn't kill the stream when the screen sleeps.

### Exit criteria

A full match streamed from the rig through the VME app alone, archive
intact, with SidelineHD not running.

---

## Step 3 — scoreboard overlay

Only after step 2 has survived a real match.

- Draw the scoreboard into a `Bitmap` with `Canvas`, hand it to
  RootEncoder's `ImageObjectFilterRender`.
- **Redraw only when the score changes**, not per frame. Nothing runs
  per-frame on the GPU except a textured quad.
- Geometry, colours, discs and the skewed dividers port from
  `backend/app/render/overlays/scoreboard.py` — it's the same layout
  problem, and matching it means the stream and the rendered video
  finally look like the same product.
- Team names, colours and logos come from the existing roster endpoints,
  so there is no second place to configure branding.
- Score input: see "Scope decisions" above — decide before starting.

---

## Step 4 (optional) — archive replaces the full-render upload

If the live archive is good enough to be *the* full match video, this
deletes the largest recurring cost in the whole system: a ~4.8 GB
`videos.insert` per match, the upload lane, `uploadLimitExceeded`
parking, and the overnight wait.

- Register the broadcast's video id on the match as its full render.
- Caveat: the archive is a ~6 Mbps H.264 re-encode. It is fine to
  *watch* and wrong to *edit*. The DSLR card stays the editing master —
  and note that if the app ever records locally, that recording has the
  overlay burned in (record taps the encoder output, after the GL
  composite), so it's not a master either.

---

## Reference: limits and costs (verified 2026)

| Thing | Value |
|---|---|
| Live API quota | 50 units per write, 1 per list — same 10,000/day pool as uploads |
| Full broadcast lifecycle | ~100 units with autoStart (no transitions) |
| Active streams per channel | 10 |
| Active streams per stream key | 3 |
| Archive | auto-saved if **< 12 h**; over 12 h may not be captured at all |
| Eligibility | verified phone, 24 h wait after first enabling, no live restrictions in 90 days |
| 50-subscriber rule | **Does not apply.** That's the YouTube *app*; API + RTMPS is encoder streaming |
| Ingest | RTMPS, port 443, SNI required; `cdn.ingestionInfo.rtmpsIngestionAddress` |
| Scopes | `youtube.force-ssl` — the credentials VME already holds |

Quota is a non-issue for streaming. The limits that bite are gym
bandwidth, phone thermals, and UVC negotiation.

---

## Deferred ideas (explicitly out of scope, recorded so they aren't re-derived)

These were attractive when the app was also going to be the tagger. It
isn't, and the export/import bridge (step 1b) covers the useful part
without any of them. Revisit only if live scoring ever reopens.

- App POSTs events to the match API **live**. Superseded by the file
  export: same data, no network dependency, no auth on the phone, no
  half-synced match to reconcile.
- Second scoring device on the rig's LAN via a local WebSocket hosted by
  the phone.
- Offline event queue on the phone, syncing to VME opportunistically.
  (The export file *is* the offline queue, flushed once, by hand.)

Still worth doing once the bridge exists, because the export carries
the broadcast's `video_id` and `started_at`:

- Auto-generated description timestamps linking into the archive with
  `&t=`, from `build_timestamps()` plus broadcast start wall-clock.
  This needs no live integration at all — just the imported events.

---

## Findings

### Step 0, PROBE — 2026-09-19. **The hardware is fine.**

- **Test device:** Samsung Galaxy S24 (SM-S921U1), Android 16 (API 36).
- **Adapter:** `345f:2130`, UltraSemi "Guermok USB2 Video", serial
  44881096. Class MISC/IAD, **6 interfaces**: 2x video (UVC), 3x audio
  (UAC), 1x HID.
- **UVC video: YES. UAC audio: YES.** Both on the one adapter, which
  settles the open question from the audio section below.
- **22 advertised formats** parsed from 1204 descriptor bytes.

The formats matter more than the count. MJPEG:

| Resolution | fps offered |
|---|---|
| **1920x1080** | **50, 30, 25, 20, 10** |
| 2560x1440 | 30, 10 |
| 1600x1200, 1360x768, 1280x1024, 1280x960, 1280x720, 1024x768, 800x600, 720x576, 720x480 | 60, 50, 30, 20, 10 |

Uncompressed (YUY2) exists at every one of those resolutions too, but
**1080p only at 10 and 5 fps** — a textbook USB 2.0 bandwidth wall, and
exactly why the source insists on MJPEG.

**1080p MJPEG at 30fps is advertised outright**, so the feared
UVCAndroid #135 scenario (1080p60-only, `err = -51`) does not apply to
this adapter. The plan's target format is available as specified.

- **USB audio:** `USB-Audio - Guermok USB2 Video`, id 371, **2 channels,
  48000 Hz**. Stereo 48k confirmed, so no resampling and no need to fall
  back to the phone mic.

### Step 0, PREVIEW — first attempt failed, in my code, not the hardware

`supportedSizeList` came back with all 22 entries and the library's
default was `Size(2560x1440@30,type:7)` — 1440p, which is neither what
the encoder wants nor what the bus should be asked for. So the source
correctly decided to reopen at `Size(1920x1080@50,type:7)`... and the
log ended at `device closed`. No preview, no exception.

Cause: `CameraHelper.closeCamera()` and `openCamera()` both *post to an
async handler*, and closing the camera also closes the device. The
queued open then found `mUsbDevice` gone and silently did nothing.
Open-then-reopen cannot work.

Fix: `getSupportedSizeList()` needs only the **device**, not an open
camera, so the format is now chosen in `onDeviceOpen` and the camera is
opened exactly once with it. Also, `Size` carries a full `fpsList`, so
the 1080p entry is requested at **30fps** rather than the 50 the library
surfaces — the encoder is configured for 30, and on USB 2.0 the surplus
frames are bandwidth spent to be thrown away.

- **`MicrophoneSource.setPreferredDevice` returned `false`** — because it
  was called before `prepareAudio` created the `AudioRecord`. It stores
  the preference and re-applies it in `start()`, so this was a
  misleading log line rather than a routing failure. Now called after
  prepare.

_(pending: preview result after the fix, then step 1)_

- RootEncoder `CameraUvcSource` result: n/a — not used; see
  `android/UvcVideoSource.kt` for why.
- Dropout 30 s:
- Dropout 3 min:
