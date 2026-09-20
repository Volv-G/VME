# VME streamer — spike harness

This is **not the app.** It is the instrument that decides whether the app
in [`docs/android-streaming-spike.md`](../docs/android-streaming-spike.md)
can be built at all, and its only real output is text to paste into that
document's "Findings" section.

Run it once, on the actual phone, with the actual HDMI adapter. If step 2
fails on every path, the whole plan changes — which is worth an evening to
learn now rather than mid-season.

## What it does

Four buttons, in order. Each can fail independently, which is deliberate:
"it doesn't work" is not a finding.

| Button | Answers |
|---|---|
| **1 PROBE** | What does the adapter actually advertise? VID/PID, UVC formats and frame rates read straight from the USB descriptors, and whether a USB **audio** interface exists at all. Uses no libraries, so it works even if everything below fails. |
| **2 PREVIEW** | Can UVCAndroid open it and produce frames? This is the **step 0 exit criterion**. |
| **3 STREAM** | Does RTMPS to YouTube hold up? The **step 2 exit criterion**. Paste an ingest URL first. |
| **COPY** | Whole log to the clipboard, and to a file under the app's external files dir. |

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

**Then, on the PC:**

```
android\deploy.cmd find                                REM shows IP:PORT
android\deploy.cmd pair    192.168.1.50:37123 123456   REM once per phone
android\deploy.cmd connect 192.168.1.50:41987          REM after each reboot
android\deploy.cmd                                     REM build+install+run+log
```

For the pairing step, tap **Pair device with pairing code** on the
phone — it shows a 6-digit code and an address. Keep that popup open;
the pairing service only exists while it is on screen.

> **The two ports are different.** The popup's port is for `pair`; the
> main Wireless debugging screen shows another for `connect`. Using the
> wrong one is the usual reason pairing looks broken. `deploy.cmd find`
> labels them: `_adb-tls-pairing` vs `_adb-tls-connect`.

Pairing is once per phone; `connect` is needed again after a reboot, or
whenever the phone changes the port, which it does freely.

### Or a cable, for the first install

*Developer options → USB debugging*, plug in, accept the RSA prompt,
then `android\deploy.cmd`. Fine for step 1 (**PROBE** needs the adapter,
so this only works on a phone with two ports or a powered hub).

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

So `UvcVideoSource` opens once with defaults to read the list, then
reopens with a chosen entry, and never re-sizes a running preview. If that
still fails, **1 PROBE** tells you whether the format was ever there —
which is the difference between "buy a different adapter" and "the library
is mis-negotiating".

## Expected failure modes, in order of likelihood

1. **No USB audio interface.** Common on cheap MS2109-class adapters. Not
   fatal: the phone mic in a gym is arguably better than a line feed.
2. **`err = -51` on preview.** Check the PROBE output first. If MJPEG
   1080p is listed and preview still fails, it is the library.
3. **1080p is uncompressed-only.** USB 2.0 cannot carry that above ~5fps.
   That is a hardware answer, not a software one.
4. **`prepareVideo` returns false.** The phone's encoder refused 1080p30
   at 6 Mbps. Drop to 720p or 3 Mbps and note it.
