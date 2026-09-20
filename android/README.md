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

## Setup

You need a JDK and the Android SDK; neither is currently installed on the
dev box. Easiest path is **Android Studio** (bundles both), then
*File → Open* this `android/` folder and let it sync.

Studio will offer to create the Gradle wrapper jar, which is gitignored.
From the command line after that:

```
cd android
./gradlew installDebug        # or gradlew.bat on Windows
adb logcat -s VMESpike        # same lines as the on-screen log
```

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
