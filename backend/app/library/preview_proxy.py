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


def proxy_path(src: Path) -> Path:
    return src.parent / PROXY_DIR / (src.name + ".mp4")


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


def status(src: Path) -> dict:
    """`native` | `ready` | `building` (with progress) | `failed` | `missing`."""
    if browser_playable(src):
        return {"state": "native", "progress": 1.0}
    if proxy_path(src).is_file():
        return {"state": "ready", "progress": 1.0}
    with _jobs_lock:
        job = _jobs.get(str(src))
    if job is None:
        return {"state": "missing", "progress": 0.0}
    return {"state": job.state, "progress": job.progress, "error": job.error}


def ensure(src: Path, duration: float) -> dict:
    """Start building the proxy if one is needed and not under way."""
    st = status(src)
    if st["state"] != "missing" and st["state"] != "failed":
        return st
    job = _Job()
    with _jobs_lock:
        current = _jobs.get(str(src))
        if current is not None and current.state == "building":
            return status(src)
        _jobs[str(src)] = job
    threading.Thread(
        target=_build, args=(src, max(duration, 0.001), job),
        name=f"proxy-{src.name}", daemon=True,
    ).start()
    return {"state": "building", "progress": 0.0}


def remove(src: Path) -> None:
    """Drop the proxy along with its clip."""
    try:
        proxy_path(src).unlink(missing_ok=True)
    except OSError:
        pass


def _build(src: Path, duration: float, job: _Job) -> None:
    dst = proxy_path(src)
    tmp = dst.with_name(dst.name + ".partial.mp4")
    with _build_slot:
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            cmd = [
                ffmpeg_path(), "-y", "-loglevel", "error", "-nostats",
                "-i", str(src),
                "-map", "0:v:0", "-map", "0:a:0?",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                "-pix_fmt", "yuv420p", "-g", "60",
                "-fps_mode", "passthrough",
                "-c:a", "aac", "-b:a", "160k", "-ac", "2",
                "-movflags", "+faststart",
                "-progress", "pipe:1",
                str(tmp),
            ]
            logger.info("building preview proxy for %s", src.name)
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
                _jobs.pop(str(src), None)
        except Exception as exc:  # noqa: BLE001 - reported to the player
            logger.warning("preview proxy failed for %s: %s", src.name, exc)
            job.state = "failed"
            job.error = str(exc)
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
