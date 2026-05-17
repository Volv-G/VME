"""Filesystem path conventions for the media library.

Layout:
    media/
        <Team>/
            roster.json
            <Match>/
                source*.mp4         (one or more clips)
                match.json
                renders/
                    *.mp4

`<Team>` and `<Match>` directory names are sanitized with `safe_segment` so
they are safe across the operating systems we care about while staying
human-readable.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..config import MEDIA_ROOT


_INVALID_FS_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_segment(name: str) -> str:
    """Convert an arbitrary string to a safe path segment.

    Replaces invalid filesystem characters with `_`, collapses whitespace, and
    strips leading/trailing dots and spaces.
    """
    cleaned = _INVALID_FS_CHARS.sub("_", name).strip(" .")
    cleaned = re.sub(r"\s+", "_", cleaned)
    return cleaned or "unnamed"


def team_dir(team: str) -> Path:
    return MEDIA_ROOT / safe_segment(team)


def match_dir(team: str, match: str) -> Path:
    return team_dir(team) / safe_segment(match)


def match_json_path(team: str, match: str) -> Path:
    return match_dir(team, match) / "match.json"


def team_roster_path(team: str) -> Path:
    return team_dir(team) / "roster.json"


def renders_dir(team: str, match: str) -> Path:
    return match_dir(team, match) / "renders"
