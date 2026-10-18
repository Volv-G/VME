"""Browser-playable stand-ins for clips the browser cannot decode.

The editor plays clips in a `<video>` element, which decodes what the
browser decodes: H.264 and (on this machine) HEVC, VP9, AV1 - in 8-bit
4:2:0. An editing codec is none of those. A DaVinci Resolve export in
DNxHR HQX (10-bit 4:2:2, ~440 Mbps) was imported as the first clip of a
match and simply did not play: no error worth the name, just a player
that never showed a frame.

So such a clip gets a proxy: an H.264 copy in `.proxies/` beside it,
which the stream endpoint serves in its place. Renders keep reading the
original through ffmpeg, which decodes DNxHR fine; the proxy exists for
the player only.

The copy keeps every frame at its original timestamp (`-fps_mode
passthrough`, no rate filter), so frame N of the proxy is frame N of
the original and every event the editor places on it lands on the same
frame in the render. Full size, because the player zooms in to read
jersey numbers; keyframes every second, because the editor scrubs.

### Smaller copies, for the editor on a slow connection

The same machinery makes reduced-size copies - 720p, 540p, 360p - which
the editor asks for when the operator picks a smaller editing size (a
browser setting; see VideoPlayer). Full HD down a hotel wifi stalls on
every seek, and the editor does not need it: it places events, it does
not judge sharpness. Renders never see these; they read the original.
Same frame-for-frame copy, keyframes every second whatever the rate,
`<name>.540p.mp4` beside `<name>.mp4`. Built only when asked for, and
kept, so the second evening on the same match is instant.

Built in the background - a quarter-hour of 1080p60 takes minutes - one
at a time, so a folder of them does not take every core at once.
Started when the clip is imported, or the first time the player asks
for a clip that predates this (see `ensure`). Written to a temporary
name and renamed on success, so a half-built proxy is never served.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .ffmpeg_tools import ffmpeg_path, ffprobe_path

logger = logging.getLogger(__name__)

PROXY_DIR = ".proxies"

_PLAYABLE_CODECS = {"h264", "hevc", "vp8", "vp9", "av1"}
_PLAYABLE_PIX_FMTS = {"yuv420p", "yuvj420p"}

# Editing sizes the editor may ask for, by frame height.
EDIT_HEIGHTS = (720, 540, 360)


@dataclass
class _Job:
    state: str = "building"  # building | failed
    progress: float = 0.0
    error: Optional[str] = None
    started: threading.Event = field(default_factory=threading.Event)


_jobs: dict[str, _Job] = {}
_jobs_lock = threading.Lock()
# One build at a time across the server.
_build_slot = threading.Semaphore(1)
# (path, mtime, size) -> playable. The stream endpoint is hit for every
# range request of every clip; probing each time would be absurd.
_playable_cache: dict[tuple[str, float, int], bool] = {}


def proxy_path(src: Path, height: Optional[int] = None) -> Path:
    """The full-size stand-in, or with `height` the reduced copy."""
    suffix = f".{height}p.mp4" if height else ".mp4"
    return src.parent / PROXY_DIR / (src.name + suffix)


def edit_height(height: Optional[int], src_height: int) -> Optional[int]:
    """The reduced size worth serving, or None for the original.

    Only the sizes the editor offers, and only below the clip's own: a
    "540p" copy of a 480p clip would be bigger and blurrier. A clip whose
    height is unknown (0) gets the copy - the build scales down, never up.
    """
    if height not in EDIT_HEIGHTS:
        return None
    if src_height and src_height <= height:
        return None
    return height


def browser_playable(src: Path) -> bool:
    """Can a browser decode this file's video as it is?

    When the probe cannot tell, the answer is yes: a needless proxy costs
    minutes of CPU, while an unneeded "cannot play" is just the old
    behaviour.
    """
    try:
        st = src.stat()
    except OSError:
        return True
    key = (str(src), st.st_mtime, st.st_size)
    hit = _playable_cache.get(key)
    if hit is not None:
        return hit
    playable = True
    try:
        out = subprocess.run(
            [ffprobe_path(), "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name,pix_fmt", "-of", "csv=p=0", str(src)],
            capture_output=True, text=True, timeout=30,
        )
        parts = (out.stdout or "").strip().split(",")
        if out.returncode == 0 and len(parts) >= 2:
            codec, pix = parts[0].strip(), parts[1].strip()
            playable = codec in _PLAYABLE_CODECS and pix in _PLAYABLE_PIX_FMTS
            if not playable:
                logger.info("%s is %s/%s: needs a preview proxy", src.name, codec, pix)
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("could not probe %s for playability: %s", src, exc)
    _playable_cache[key] = playable
    return playable


def status(src: Path, height: Optional[int] = None) -> dict:
    """`native` | `ready` | `building` (with progress) | `failed` | `missing`.

    With `height` (already passed through [edit_height]), about the
    reduced copy, which every clip can have, playable or not.
    """
    if height is None and browser_playable(src):
        return {"state": "native", "progress": 1.0}
    dst = proxy_path(src, height)
    if dst.is_file():
        return {"state": "ready", "progress": 1.0}
    with _jobs_lock:
        job = _jobs.get(str(dst))
    if job is None:
        return {"state": "missing", "progress": 0.0}
    return {"state": job.state, "progress": job.progress, "error": job.error}


def ensure(src: Path, duration: float, height: Optional[int] = None) -> dict:
    """Start building the proxy if one is needed and not under way."""
    st = status(src, height)
    if st["state"] != "missing" and st["state"] != "failed":
        return st
    dst = proxy_path(src, height)
    job = _Job()
    with _jobs_lock:
        current = _jobs.get(str(dst))
        if current is not None and current.state == "building":
            return status(src, height)
        _jobs[str(dst)] = job
    threading.Thread(
        target=_build, args=(src, max(duration, 0.001), job, height),
        name=f"proxy-{dst.name}", daemon=True,
    ).start()
    return {"state": "building", "progress": 0.0}


def remove(src: Path) -> None:
    """Drop every proxy of a clip along with it: the full-size stand-in
    and any reduced copies."""
    folder = src.parent / PROXY_DIR
    try:
        for p in folder.iterdir():
            if p.name.startswith(src.name + "."):
                p.unlink(missing_ok=True)
    except OSError:
        pass


def _build(src: Path, duration: float, job: _Job, height: Optional[int] = None) -> None:
    dst = proxy_path(src, height)
    tmp = dst.with_name(dst.name + ".partial.mp4")
    with _build_slot:
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if height:
                # Small and quick to seek: a keyframe every second
                # whatever the frame rate, and a bitrate that suits the
                # connection it is for.
                video = [
                    "-vf", f"scale=-2:{height}",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
                    "-pix_fmt", "yuv420p",
                    "-force_key_frames", "expr:gte(t,n_forced*1)",
                ]
                audio = ["-c:a", "aac", "-b:a", "96k", "-ac", "2"]
            else:
                video = [
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                    "-pix_fmt", "yuv420p", "-g", "60",
                ]
                audio = ["-c:a", "aac", "-b:a", "160k", "-ac", "2"]
            cmd = [
                ffmpeg_path(), "-y", "-loglevel", "error", "-nostats",
                "-i", str(src),
                "-map", "0:v:0", "-map", "0:a:0?",
                *video,
                "-fps_mode", "passthrough",
                *audio,
                "-movflags", "+faststart",
                "-progress", "pipe:1",
                str(tmp),
            ]
            logger.info("building preview proxy %s", dst.name)
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                if line.startswith("out_time_us="):
                    try:
                        us = int(line.split("=", 1)[1])
                        job.progress = max(0.0, min(0.99, us / 1e6 / duration))
                    except ValueError:
                        pass
            err = proc.stderr.read() if proc.stderr else ""
            code = proc.wait()
            if code != 0:
                raise RuntimeError(f"ffmpeg exited {code}: {err.strip()[-400:]}")
            os.replace(tmp, dst)
            logger.info("preview proxy ready: %s", dst)
            with _jobs_lock:
                _jobs.pop(str(dst), None)
        except Exception as exc:  # noqa: BLE001 - reported to the player
            logger.warning("preview proxy failed for %s: %s", src.name, exc)
            job.state = "failed"
            job.error = str(exc)
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
