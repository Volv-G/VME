"""Load encoder settings for the current platform."""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


_DEFAULTS: dict[str, dict[str, Any]] = {
    "windows": {
        "codec": "libx264",
        "preset": "medium",
        "bitrate": "4500k",
        "audio": True,
        "audio_codec": "aac",
        "audio_bitrate": "128k",
        "threads": 8,
        "ffmpeg_params": [
            "-profile:v", "high",
            "-level", "4.1",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
        ],
    }
}


def _platform_key() -> str:
    return {"win32": "windows", "darwin": "darwin", "linux": "linux"}.get(sys.platform, sys.platform)


def load_settings(path: str | Path | None = None) -> dict[str, Any]:
    if path is None:
        path = Path(__file__).parent / "render_settings.json"
    path = Path(path)
    key = _platform_key()
    fallback = _DEFAULTS.get(key, _DEFAULTS["windows"])

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        logger.warning("Could not load render settings %s: %s", path, exc)
        return dict(fallback)

    raw = data.get(key, fallback)
    merged = dict(fallback)
    merged.update(raw if isinstance(raw, dict) else {})
    return merged
