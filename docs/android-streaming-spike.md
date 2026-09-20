# Android streaming app — spike plan

Status: **not started.** Parked for future execution.

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

1. Build and run the **UVCAndroid demo app** on the actual phone with
   the actual adapter.
2. Log the full `supportedSizeList` — every entry's width, height, fps
   and `type`. Paste it into this file under "Findings".
3. Run RootEncoder's `rotation` example → Video Source → **CameraUVC**.
4. If negotiation fails, walk the list: MJPEG first, then lower
   resolutions, then let the library pick (omit `setPreviewSize`
   entirely — that resolved it for some reporters).

### Also in the same evening: audio

HDMI capture audio arrives as a **separate USB audio (UAC) device**, not
inside the UVC video stream. RootEncoder's `MicrophoneSource` will not
pick it up by default.

- Test `AudioRecord` with `setPreferredDevice()` on the
  `AudioDeviceInfo.TYPE_USB_DEVICE` entry, or write a custom
  `AudioSource`.
- Confirm the adapter actually exposes an audio interface at all — some
  cheap ones don't, in which case the audio has to come from the phone
  mic (which for a gym is arguably fine, possibly better).

### What to record

- Adapter chipset (MS2109-class = USB 2.0, typically 1080p30 MJPEG only,
  1080p5 uncompressed; MS2130/USB3-class = easier).
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

## Step 2 — UVC → RTMPS → YouTube, no overlay

Smallest thing that replaces both apps.

**Backend** (`backend/app/api/live.py`, new):

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
isn't. Revisit only if that changes.

- App emits `ball_served` / `kill` / `ace` / … to the match API, so
  `match.json` arrives pre-tagged and reels render the same evening.
- Auto-generated description timestamps linking into the archive with
  `&t=`, from `build_timestamps()` plus broadcast start wall-clock.
- Second scoring device on the rig's LAN via a local WebSocket hosted by
  the phone, so scoring survives losing internet.
- Offline event queue on the phone, syncing to VME opportunistically.

---

## Findings

_(fill in during step 0 / step 1)_

- Adapter chipset:
- Test device + Android version:
- `supportedSizeList`:
- UVCAndroid demo result:
- RootEncoder `CameraUvcSource` result:
- USB audio device enumerated / captured:
- Dropout 30 s:
- Dropout 3 min:
