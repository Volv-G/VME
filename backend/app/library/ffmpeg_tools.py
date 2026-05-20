"""Locate `ffmpeg` and `ffprobe` executables.

Background
----------
The renderer uses `moviepy`, which calls into `imageio_ffmpeg` to get a
bundled ffmpeg binary - that path is self-contained. But the *editor*
side of the app shells out to `ffmpeg` (transcoding to the project fps)
and especially `ffprobe` (probing uploaded clips), and `imageio_ffmpeg`
does NOT bundle ffprobe. So we need a way to find a system ffmpeg/
ffprobe pair that's robust to:

  * "It works in my dev shell but breaks as a Windows service" - the dev
    shell has a user-PATH entry pointing at `C:\\Programs\\ffmpeg-*\\bin`,
    the service inherits only the machine PATH and finds nothing. This
    silently downgrades probe() to None and falls back to 30 fps /
    0 frames, which manifests as "imported clip has wrong fps" and
    "rescan doesn't populate the timeline".
  * Different package managers / install locations (scoop, chocolatey,
    a manual unzip into `C:\\Programs`, a GitHub-Actions install, etc.).
  * The user wanting to override the choice without editing source -
    e.g. testing a different ffmpeg build.

Resolution order
----------------
  1. `VME_FFPROBE` / `VME_FFMPEG` environment variable (explicit).
  2. `shutil.which` against the current PATH.
  3. Common Windows install roots (Programs, Program Files, scoop, choco).
  4. The bare executable name as a last resort (lets subprocess produce
     its own clear "file not found" error, which we then log and surface
     to the caller).

The resolved path is logged once at INFO level the first time the
function is called, so a misconfigured service is one log line away
from being diagnosed instead of "why is everything 30 fps".
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Cache the resolved paths so we don't log the banner on every probe.
_resolved: dict[str, str] = {}

# Likely Windows install roots. Walked in order; first hit wins. Each
# entry is a glob pattern relative to one of the standard prefixes.
_WINDOWS_GLOB_ROOTS = [
    r"C:\Programs",
    r"C:\Program Files",
    r"C:\Program Files (x86)",
    os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Packages"),
    os.path.expandvars(r"%USERPROFILE%\scoop\apps\ffmpeg"),
    os.path.expandvars(r"%USERPROFILE%\scoop\shims"),
    r"C:\ProgramData\chocolatey\bin",
]


def _hunt_windows(exe_name: str) -> Optional[str]:
    """Search common Windows install roots for `<exe_name>.exe`.

    Uses simple `rglob` rather than walking with depth limits: ffmpeg
    distributions nest the binary in a `bin/` subfolder of a versioned
    directory (e.g. `ffmpeg-master-latest-win64-gpl-shared\\bin\\`), so
    we can't predict the exact depth. `rglob` is fine here because the
    roots we check are small and only walked once per process.
    """
    target = f"{exe_name}.exe"
    for root_str in _WINDOWS_GLOB_ROOTS:
        root = Path(root_str)
        if not root.exists():
            continue
        try:
            # Direct bin/ hits first (cheap), then a broader walk.
            for candidate in [root / "bin" / target, root / target]:
                if candidate.is_file():
                    return str(candidate)
            for candidate in root.rglob(target):
                if candidate.is_file():
                    return str(candidate)
        except (PermissionError, OSError):
            # Some Program Files subtrees aren't readable by the service
            # user; just skip and move on.
            continue
    return None


def _resolve(exe_name: str, env_var: str) -> str:
    """Resolve a single executable. Cached. Logs the choice once."""
    if exe_name in _resolved:
        return _resolved[exe_name]

    # 1. Explicit override.
    override = os.environ.get(env_var, "").strip()
    if override:
        if Path(override).is_file():
            logger.info("%s resolved via %s -> %s", exe_name, env_var, override)
            _resolved[exe_name] = override
            return override
        logger.warning(
            "%s=%r is set but the file does not exist; falling through",
            env_var,
            override,
        )

    # 2. PATH lookup.
    on_path = shutil.which(exe_name)
    if on_path:
        logger.info("%s resolved via PATH -> %s", exe_name, on_path)
        _resolved[exe_name] = on_path
        return on_path

    # 3. Windows install-root hunt.
    if os.name == "nt":
        hunted = _hunt_windows(exe_name)
        if hunted:
            logger.info(
                "%s not on PATH; resolved by scanning install roots -> %s. "
                "Consider setting %s=%s in secrets.env to skip the scan.",
                exe_name,
                hunted,
                env_var,
                hunted,
            )
            _resolved[exe_name] = hunted
            return hunted

    # 4. Give up - let subprocess raise the real error so the caller
    # sees a sensible message. Don't cache the failure: a later install
    # might put the binary somewhere we'd find, and worker processes are
    # long-lived.
    logger.error(
        "%s not found on PATH and not in any standard install root. "
        "Probe / transcode / render calls that depend on it WILL FAIL. "
        "Set %s=<full path to %s.exe> in secrets.env and reinstall the "
        "service.",
        exe_name,
        env_var,
        exe_name,
    )
    return exe_name


def ffprobe_path() -> str:
    return _resolve("ffprobe", "VME_FFPROBE")


def ffmpeg_path() -> str:
    return _resolve("ffmpeg", "VME_FFMPEG")
