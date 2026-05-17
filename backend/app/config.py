"""Application configuration.

Path layout is driven by env vars. The single most useful knob is
``VME_DATA_DIR``: set that to one folder and everything mutable (uploaded
clips, match.json sidecars, logs, certs, ...) lives there. Each individual
subdir can still be redirected with its own env var if needed.

Resolution order for each path:

    1. its own explicit env var (e.g. ``VME_MEDIA_ROOT``)
    2. ``<VME_DATA_DIR>/<subdir>`` if ``VME_DATA_DIR`` is set
    3. ``<PROJECT_ROOT>/<subdir>`` (legacy layout, repo-relative)

``STATIC_ROOT`` (built frontend assets) is *not* data: it stays under the
project by default and isn't affected by ``VME_DATA_DIR``.
"""

from __future__ import annotations

import os
from pathlib import Path


def _root_default() -> Path:
    """Default project root: parent of `backend/`."""
    return Path(__file__).resolve().parent.parent.parent


def _resolve(env_var: str, data_subdir: str, legacy_default: Path) -> Path:
    """Pick a path per the documented resolution order."""
    explicit = os.environ.get(env_var)
    if explicit:
        return Path(explicit).resolve()
    if DATA_DIR is not None:
        return (DATA_DIR / data_subdir).resolve()
    return legacy_default.resolve()


PROJECT_ROOT = Path(os.environ.get("VME_PROJECT_ROOT", _root_default())).resolve()

# Single switch: when set, media/logs/certs all default under here.
_data_dir_env = os.environ.get("VME_DATA_DIR")
DATA_DIR: Path | None = Path(_data_dir_env).resolve() if _data_dir_env else None

MEDIA_ROOT = _resolve("VME_MEDIA_ROOT", "media", PROJECT_ROOT / "media")
LOG_ROOT = _resolve("VME_LOG_ROOT", "logs", PROJECT_ROOT / "logs")
CERT_DIR = _resolve("VME_CERT_DIR", "certs", PROJECT_ROOT / "certs")

# Static frontend bundle is build output, not user data - keep it project-local.
STATIC_ROOT = Path(
    os.environ.get("VME_STATIC_ROOT", PROJECT_ROOT / "backend" / "static")
).resolve()

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm"}

if DATA_DIR is not None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
MEDIA_ROOT.mkdir(parents=True, exist_ok=True)
LOG_ROOT.mkdir(parents=True, exist_ok=True)
