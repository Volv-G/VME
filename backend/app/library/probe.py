"""Probe video files for metadata using ffprobe (bundled with imageio-ffmpeg)."""

from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class VideoInfo:
    width: int
    height: int
    fps: float
    frame_count: int
    duration: float
    # Unix timestamp (UTC) of when the recording STARTED. We prefer the
    # container's `creation_time` tag (set by most cameras when they begin
    # writing the file); when that's missing we fall back to the file's
    # mtime minus `duration` as a reasonable approximation.
    start_recording_time: Optional[float] = None


def _ffprobe_path() -> str:
    """Return path to ffprobe.

    `imageio-ffmpeg` bundles ffmpeg only; ffprobe must be on PATH (the user
    has it). We fall back to plain `ffprobe` if it is.
    """
    return "ffprobe"


def probe(path: str | Path) -> Optional[VideoInfo]:
    """Probe a video file. Returns None if probing fails."""
    path_str = str(path)
    try:
        out = subprocess.run(
            [
                _ffprobe_path(),
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height,r_frame_rate,nb_frames,duration",
                "-show_entries",
                "stream_tags=creation_time",
                "-show_entries",
                "format=duration",
                "-show_entries",
                "format_tags=creation_time",
                "-of",
                "json",
                path_str,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.warning("ffprobe failed for %s: %s", path_str, exc)
        return None

    if out.returncode != 0:
        logger.warning("ffprobe error for %s: %s", path_str, out.stderr)
        return None

    try:
        data = json.loads(out.stdout)
        stream = (data.get("streams") or [{}])[0]
        fmt = data.get("format", {})
        width = int(stream.get("width") or 0)
        height = int(stream.get("height") or 0)
        fps = _parse_fps(stream.get("r_frame_rate", "0/1"))
        duration = float(stream.get("duration") or fmt.get("duration") or 0.0)
        nb_frames_raw = stream.get("nb_frames")
        if nb_frames_raw and str(nb_frames_raw).isdigit():
            frame_count = int(nb_frames_raw)
        else:
            frame_count = int(round(duration * fps)) if fps else 0

        creation_iso = (
            (stream.get("tags") or {}).get("creation_time")
            or (fmt.get("tags") or {}).get("creation_time")
        )
        start_ts = _parse_iso_timestamp(creation_iso) if creation_iso else None
        if start_ts is None:
            # Fall back to mtime - duration; that's roughly when the camera
            # started writing (mtime is updated at recording end on most
            # filesystems).
            try:
                mtime = os.path.getmtime(path_str)
                start_ts = mtime - duration
            except OSError:
                start_ts = None

        return VideoInfo(
            width=width,
            height=height,
            fps=fps or 30.0,
            frame_count=frame_count,
            duration=duration,
            start_recording_time=start_ts,
        )
    except (ValueError, KeyError, TypeError) as exc:
        logger.warning("failed to parse ffprobe output for %s: %s", path_str, exc)
        return None


def _parse_iso_timestamp(s: str) -> Optional[float]:
    """Parse an ISO-8601 timestamp from ffprobe into a unix timestamp.

    Handles the trailing 'Z' (UTC) shorthand which `fromisoformat` accepts
    only in Python 3.11+ but defensively normalize to be safe.
    """
    if not s:
        return None
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _parse_fps(raw: str) -> float:
    if not raw or "/" not in raw:
        try:
            return float(raw or 0)
        except ValueError:
            return 0.0
    num, den = raw.split("/")
    try:
        n, d = float(num), float(den)
        return n / d if d else 0.0
    except ValueError:
        return 0.0
