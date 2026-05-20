"""Re-encode a video file to a target frame rate using ffmpeg.

Used when a clip is uploaded to a match whose project FPS is already set
(by the first clip) and the new file's FPS differs. Mixing fps inside a
match would break all the frame->time math the editor and renderer rely
on, so we normalize on ingest.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from .ffmpeg_tools import ffmpeg_path

logger = logging.getLogger(__name__)


def _ffmpeg_path() -> str:
    """Return path to ffmpeg. Delegates to the shared resolver in
    `app.library.ffmpeg_tools` so probe + transcode share the same
    discovery logic and the same VME_FFMPEG override."""
    return ffmpeg_path()


def transcode_to_fps(src: Path, target_fps: float) -> bool:
    """Re-encode `src` in place so its frame rate is `target_fps`.

    Writes to a sibling temp file, then atomically replaces the original
    on success. Returns True on success, False on any failure - the caller
    decides whether to surface a user-facing error.

    Encoding choices:
      - `-vf fps=<target>` rather than `-r <target>` so variable-fps
        sources are correctly resampled (duplicate / drop frames as
        needed) instead of just being mislabeled.
      - libx264 CRF 18, medium preset: visually lossless for camera
        footage at a manageable file size.
      - audio re-encoded to AAC 192k - copying through can fail when the
        source audio codec is something mp4 won't accept (rare with
        camera output but cheap insurance).
      - `+faststart` so the moov atom moves to the front, letting the
        browser stream the file without downloading the whole tail first.
    """
    src = Path(src)
    tmp = src.with_name(src.stem + ".transcoded" + src.suffix)
    cmd = [
        _ffmpeg_path(),
        "-y",
        "-i",
        str(src),
        "-vf",
        f"fps={target_fps:.6f}",
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(tmp),
    ]
    logger.info(
        "transcoding %s -> %.3f fps (%s)", src.name, target_fps, tmp.name
    )
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=3600
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.warning("ffmpeg launch failed for %s: %s", src, exc)
        _safe_unlink(tmp)
        return False
    if result.returncode != 0:
        # ffmpeg dumps a lot to stderr; keep just the tail in the log.
        tail = (result.stderr or "")[-800:]
        logger.warning("ffmpeg failed (%d): %s", result.returncode, tail)
        _safe_unlink(tmp)
        return False

    try:
        # `shutil.move` handles cross-device renames; on the same dir
        # this is effectively an atomic rename on Windows + POSIX.
        shutil.move(str(tmp), str(src))
    except OSError as exc:
        logger.warning("replace failed for %s: %s", src, exc)
        _safe_unlink(tmp)
        return False
    return True


def _safe_unlink(p: Path) -> None:
    try:
        p.unlink(missing_ok=True)
    except OSError:
        pass
