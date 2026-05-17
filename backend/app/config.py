"""Application configuration."""

from __future__ import annotations

import os
from pathlib import Path


def _root_default() -> Path:
    """Default project root: parent of `backend/`."""
    return Path(__file__).resolve().parent.parent.parent


PROJECT_ROOT = Path(os.environ.get("VME_PROJECT_ROOT", _root_default())).resolve()
MEDIA_ROOT = Path(os.environ.get("VME_MEDIA_ROOT", PROJECT_ROOT / "media")).resolve()
STATIC_ROOT = Path(os.environ.get("VME_STATIC_ROOT", PROJECT_ROOT / "backend" / "static")).resolve()
LOG_ROOT = Path(os.environ.get("VME_LOG_ROOT", PROJECT_ROOT / "logs")).resolve()
CERT_DIR = Path(os.environ.get("VME_CERT_DIR", PROJECT_ROOT / "certs")).resolve()

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm"}

MEDIA_ROOT.mkdir(parents=True, exist_ok=True)
LOG_ROOT.mkdir(parents=True, exist_ok=True)
