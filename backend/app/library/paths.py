"""Filesystem path conventions for the media library.

Layout:
    media/
        <Team>/
            roster.json
            <Tournament>/
                <Date>/
                    <Opponent>/
                        source*.mp4         (one or more clips)
                        match.json
                        renders/
                            *.mp4

Every path segment (`<Team>`, `<Tournament>`, `<Date>`, `<Opponent>`) is
sanitized with `safe_segment` so it's safe across the operating systems we
care about while staying human-readable.

The "match" identifier in URLs and API calls is the opponent (the leaf folder
name); the `<Date>` segment lives between the tournament and the match so
multiple matches against the same opponent on different days don't collide.
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


# Match folders are named `<NN>_<Opponent>` where NN is the 1-based order
# of the match on that date. The numeric prefix keeps the on-disk listing
# sorted in play order regardless of opponent name.
_MATCH_FOLDER_RE = re.compile(r"^(\d+)_(.+)$")


def parse_match_folder(folder_name: str) -> tuple[int | None, str]:
    """Split a match folder name into `(index, opponent_part)`.

    Returns `(None, folder_name)` for legacy folders that have no numeric
    prefix.
    """
    m = _MATCH_FOLDER_RE.match(folder_name)
    if not m:
        return None, folder_name
    return int(m.group(1)), m.group(2)


def format_match_folder(index: int, opponent: str) -> str:
    """Build a match folder name from an index and opponent."""
    return f"{index:02d}_{safe_segment(opponent)}"


def team_dir(team: str) -> Path:
    return MEDIA_ROOT / safe_segment(team)


def tournament_dir(team: str, tournament: str) -> Path:
    return team_dir(team) / safe_segment(tournament)


def date_dir(team: str, tournament: str, date: str) -> Path:
    return tournament_dir(team, tournament) / safe_segment(date)


def match_dir(team: str, tournament: str, date: str, match: str) -> Path:
    return date_dir(team, tournament, date) / safe_segment(match)


def match_json_path(team: str, tournament: str, date: str, match: str) -> Path:
    return match_dir(team, tournament, date, match) / "match.json"


def team_roster_path(team: str) -> Path:
    return team_dir(team) / "roster.json"


def renders_dir(team: str, tournament: str, date: str, match: str) -> Path:
    return match_dir(team, tournament, date, match) / "renders"
