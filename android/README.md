# VME streamer — spike harness

This is **not the app.** It is the instrument that decides whether the app
in [`docs/android-streaming-spike.md`](../docs/android-streaming-spike.md)
can be built at all, and its only real output is text to paste into that
document's "Findings" section.

Run it once, on the actual phone, with the actual HDMI adapter. If step 2
fails on every path, the whole plan changes — which is worth an evening to
learn now rather than mid-season.

## What it does

Buttons run in order. Each can fail independently, which is deliberate:
"it doesn't work" is not a finding.

| Button | Answers |
|---|---|
| **1 PREVIEW** | Can UVCAndroid open the adapter and produce frames? This is the **step 0 exit criterion**. |
| **2 STREAM** | Does RTMPS to YouTube hold up? The **step 2 exit criterion**. The ingest URL is prefilled from `local.properties`. |
| **3 GO LIVE** | Create a **titled** broadcast from the phone, bind it to the channel's stream key, and start streaming to it. Needs the one-time setup below. |
| **COPY** | Whole log to the clipboard, and to a file under the app's external files dir. |

## GO LIVE: creating the broadcast from the phone

A persistent stream key makes YouTube auto-create a broadcast when bytes
arrive - which inherits whatever title is saved on the stream, so a season
ends up as a column of identically named videos. **3 GO LIVE** instead
creates the broadcast first, with the title in the text field, and binds it
to the key before streaming.

The title is decided at the gym, which is the only place the opponent is
reliably known.

### What it does not need

No OAuth client secret, and **no refresh token**. Play Services mints a
short-lived access token on the device; it lasts about an hour and is never
persisted. That is long enough, because the API is only used to *create* the
broadcast - once RTMP bytes are flowing, YouTube authenticates them by the
stream key alone, so a two-hour match outlives the token harmlessly.

Google's installed-app guide rules out the alternatives: custom URI schemes
are no longer supported on Android, and loopback redirects are deprecated for
mobile, so AppAuth's usual redirect flows do not apply to Google here.

### One-time setup: register an Android OAuth client

Google identifies the app by its **package name and signing certificate**,
not by a client id in the code - which is why there is no client id to leak.
That registration has to exist, once per signing key:

1. Google Cloud console -> the project that already holds the VME OAuth
   client -> **APIs & Services -> Credentials**.
2. **Create credentials -> OAuth client ID -> Android**.
3. Package name: `works.vme.streamer`
4. SHA-1 of the debug keystore:

   ```
   keytool -list -v -keystore %USERPROFILE%\.android\debug.keystore \
       -alias androiddebugkey -storepass android -keypass android
   ```

   On this machine that is
   `10:1F:04:59:36:4C:15:73:FA:2E:18:31:54:87:6C:48:01:B8:F6:EA`.
   **The debug keystore is per machine** - building on a different computer
   produces a different fingerprint and needs its own client entry.
5. The Google account signing in must be a **test user** on the consent
   screen if it is still in "Testing" status, and must own the channel.

If this registration is missing or mismatched, Play Services fails with
`ApiException: 10` (DEVELOPER_ERROR) and says nothing about why. The app
translates that one case, because the raw message names neither the package
nor the certificate.

### Quota

`liveStreams.list` is 1 unit, `liveBroadcasts.insert` 50, `bind` 50 - about
**100 per match** against 10,000/day. A `videos.insert` upload costs 1600,
so this is noise by comparison.

There was a **PROBE** button that dumped raw USB descriptors with no
library involved. It answered its question - this adapter advertises
MJPEG 1080p at 30fps and carries a UAC audio interface - and was removed
once preview worked. If a different adapter misbehaves, restore
`UsbProbe.kt` from commit `f0a48c2`; guessing from `err = -51` is what
it was built to avoid.

## The stream URL

Put it in `android/local.properties`, which is gitignored:

```
vme.streamUrl=rtmps://a.rtmps.youtube.com:443/live2/xxxx-xxxx-xxxx-xxxx
```

It is baked into `BuildConfig` and prefills the field, so nothing has to
be typed on a phone keyboard. **Not** committed to a source file: this
repo is public, and a stream key lets anyone broadcast to the channel.
The field stays editable for one-off tests.

From YouTube Studio: **Create -> Go live -> Stream** tab, then join the
*Stream URL* and *Stream key* with a slash. That key is persistent -
connecting to it creates a broadcast automatically, so there is no need
to click "Go live" before each match.

## Build

Once, to install a self-contained toolchain (JDK 17, Android SDK 36,
build-tools, platform-tools, Gradle). **No admin rights needed** — it all
lands in `%LOCALAPPDATA%\vme-android` and touches neither PATH nor the
registry:

```
powershell -NoProfile -ExecutionPolicy Bypass -File android\scripts\bootstrap.ps1
```

Then:

```
android\build.cmd            REM debug APK
android\build.cmd clean
android\deploy.cmd           REM build + install + launch + tail logcat
```

Android Studio also works — *File → Open* the `android/` folder — but it
is not required, and the command line above is what has actually been
exercised, including once from scratch into an empty directory to prove
the clean-machine path. To undo everything: `Remove-Item -Recurse -Force
$env:LOCALAPPDATA\vme-android`.

Set `VME_ANDROID_TOOLCHAIN` (and `bootstrap.ps1 -ToolchainDir`) to put
the toolchain somewhere else — both scripts honour it.

The bootstrap is re-runnable and skips what it already has. It is also
the thing to run after pulling a change to the SDK or Gradle version;
the Gradle version is read from `gradle-wrapper.properties` so there is
only one place to edit it.

### Versions are not negotiable

AGP 8.13.2 / Gradle 8.13 / Kotlin **2.3.21** / compileSdk **36**. Two
independent constraints force this, both discovered by the compiler
rather than by reading:

- RootEncoder 2.7.5 is built against **API 36**, so all seven of its
  modules reject `compileSdk = 35` outright.
- JitPack built it with **Kotlin 2.3**, so its `.kotlin_module` metadata
  is version 2.3.0 and any older compiler refuses the dependency with
  *"Module was compiled with an incompatible version of Kotlin"*. It
  pulls `kotlin-stdlib 2.3.21` transitively, which is the tell.

The first attempt pinned AGP 8.7.3 / Kotlin 1.9.25 on the theory that
older is safer for a spike. It did not compile.

## Deploy to the phone

### Use wireless debugging, not the cable

This matters more here than in a normal Android project: **the HDMI
capture adapter occupies the phone's only USB port.** For the half of
the testing that involves actual capture, a debugging cable cannot be
plugged in at the same time. Pair over Wi-Fi once and you can rebuild,
reinstall and read logcat with the adapter still attached.

**On the phone, once:**

1. *Settings → About phone* → tap **Build number** seven times. It
   counts down at you, then says "You are now a developer".
2. *Settings → System → Developer options* → turn on **Wireless
   debugging**. (Samsung and others bury Developer options elsewhere;
   searching settings for "developer" finds it.)
3. Phone and PC must be on the **same Wi-Fi**, and not a guest network
   that isolates clients.

**Then, on the PC.** Tap **Pair device with pairing code** on the phone
and leave that popup on screen — the pairing service exists only while
it is open — then, with the 6 digits it shows:

```
android\deploy.cmd pair <those 6 digits>   REM once per phone
android\deploy.cmd connect                 REM again after each reboot
android\deploy.cmd                         REM build+install+run+log
```

No addresses to type: both are discovered over mDNS.
`android\deploy.cmd find` shows what it can see and what to do next.

> **Why no address argument.** A phone advertises *two* services on
> *different* ports — `_adb-tls-pairing`, only while the popup is open,
> and `_adb-tls-connect`, whenever wireless debugging is on. Giving adb
> the connect port when it wanted the pairing one fails with
> `protocol fault (couldn't read status message)`, which names neither
> the cause nor the fix. Passing `HOST:PORT` by hand still works (for
> networks that block mDNS), and that mistake is detected and explained
> rather than merely documented.

Pairing is once per phone. `connect` is needed again after a reboot, or
whenever the phone changes its port, which it does freely.

### Or a cable, for the first install

*Developer options → USB debugging*, plug in, accept the RSA prompt,
then `android\deploy.cmd`. Only useful on a phone with two ports or a
powered hub, since the adapter wants the same socket.

### Or no computer at all

`android\deploy.cmd apk` prints the APK path. Copy that file to the
phone by any means and tap it; allow installing from unknown sources.
The on-screen log pane and the **COPY** button exist precisely so the
app is useful with no adb attached — COPY also writes a timestamped
file under the app's external files directory.

Install is run with `-g`, which pre-grants the microphone permission so
the harness does not stop to ask.

If install fails with `INSTALL_FAILED_UPDATE_INCOMPATIBLE`, the phone
has a build signed with a different debug key:
`adb uninstall works.vme.streamer`.

## Reading the output

The on-screen pane and logcat carry the same lines. `deploy.cmd log`
tails the tags worth seeing — the harness's own `VMESpike` plus the
native UVC tags, since the interesting failures happen down in
`libuvc` and never surface as Kotlin exceptions.

## Why it does not use `CameraUvcSource`

RootEncoder ships one, and it is 28 lines: it calls bare `openCamera()`
and its `create()` ignores the width/height/fps passed to `prepareVideo`.
Those configure the **encoder**, not the camera, so the stream silently
upscales whatever the adapter defaulted to.

The reported failure on HDMI capture cards (UVCAndroid #135, open as of
Jan 2026) is `could not negotiate with camera: err = -51`, and those cards
frequently advertise 1080p at **60fps only** — so asking for 30 throws
`Failed to set preview size`. The library author's guidance (UVCAndroid
\#1) is that `setPreviewSize` is for changing format mid-preview and
`openCamera(Size)` is the right call, with a `Size` taken whole from the
enumerated list.

Two attempts got this wrong before hardware settled it. Closing and
reopening cannot work: both calls post to an async handler and closing
the camera also closes the *device*, so the queued open finds nothing.
Choosing the format before opening cannot work either:
`getSupportedSizeList()` returns null until `mUVCCamera` exists, which
means until the camera is open.

So `UvcVideoSource` opens with defaults, reads the list in
`onCameraOpen` where it exists, applies the choice with
`setPreviewSize`, **and remembers it** so the next open can pass it to
`openCamera(Size)` directly. The remembering is a safety property:
`CameraInternal` destroys the camera outright if `setPreviewSize` is
refused, so the aim is to need it at most once per adapter.

## Expected failure modes, in order of likelihood

1. **No USB audio interface.** Common on cheap MS2109-class adapters. Not
   fatal: the phone mic in a gym is arguably better than a line feed.
2. **`err = -51` on preview.** Restore `UsbProbe.kt` (see above) before
   theorising. If MJPEG 1080p is listed in the descriptors and preview
   still fails, it is the library, not the adapter.
3. **1080p is uncompressed-only.** USB 2.0 cannot carry that above ~5fps.
   That is a hardware answer, not a software one.
4. **`prepareVideo` returns false.** The phone's encoder refused 1080p30
   at 6 Mbps. Tap the rate readout in the header and pick a lower
   bandwidth (see below), then Start again.

### Bandwidth

The header's rate readout is a button. It opens the bandwidth menu.
Each entry is a whole encoder configuration -- resolution, frame rate
and keyframe interval as well as bitrate, because at the bottom of the
range the frame size is what decides whether the scoreboard is
readable: 6 Mbps 1080p30, 3 Mbps 720p30, 1.5 Mbps 720p30, 0.6 Mbps
540p20, plus two settings for a hall whose wifi cannot carry video at
all -- **Court positions**, which replaces the picture
with the rotation as a diagram (faces, numbers, and a yellow ring on
the server), and **Overlays only**, which blacks it out entirely. Both
still stream the scoreboard, pop-ups and cards, and both cost almost
nothing on the wire because the picture only changes when somebody
moves. The choice sticks per device. Bitrate applies live
(`setVideoBitrateOnFly`) when a stream is already running; frame size,
rate and keyframe interval are baked into `prepareVideo`, so they take
effect at the next Start.

### Losing the adapter mid-match

Unplugging the capture adapter no longer ends the broadcast. A USB
watcher sees the video interface go, swaps the video source for a
still black frame (`BitmapSource` -- *not* `NoVideoSource`, which
delivers no frames at all and would stall the encoder until YouTube
dropped the stream), forces the court view up over it, and falls back
to the phone's microphone. Plug it back in and it returns to the
camera and the adapter's audio, the latter after a short delay because
the USB audio interface enumerates a moment behind the video one.

A YouTube broadcast that ends cannot be resumed -- it needs a new one,
with a new link, while the match carries on without it -- which is why
this is worth the machinery.

The same menu's neutral button toggles **H.264 / H.265**. HEVC is worth
roughly a third of the bitrate for the same picture and reaches YouTube
over enhanced RTMP, which RootEncoder speaks -- but the phone's encoder
and the ingest have to agree, and when they do not the broadcast simply
never comes up. Off by default; test it on a throwaway broadcast before
trusting it at a match.
