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
from datetime import datetime
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


# Date inputs we accept when reformatting `{date}` for filenames /
# titles. Folders are conventionally written as `YYYY-MM-DD` (ISO), but
# the library is lenient about typos and legacy uses, so we accept a
# few common alternates.
_DATE_INPUT_FORMATS = (
    "%Y-%m-%d",     # canonical folder slug, e.g. 2026-05-17
    "%Y.%m.%d",     # already-dotted form (idempotent reformat)
    "%Y_%m_%d",     # underscored
    "%Y/%m/%d",     # forward-slash
    "%d-%m-%Y",     # day-first variants - rarer but harmless to accept
    "%d.%m.%Y",
    "%m/%d/%Y",     # US-style
)


def format_date_for_template(date: str) -> str:
    """Reformat a date string to `YYYY.MM.DD` for use in filenames /
    titles.

    Match folders live on disk as `YYYY-MM-DD` (the canonical ISO
    slug) but the rendered output - and YouTube titles - read better
    with `YYYY.MM.DD`. This helper is the single place that conversion
    happens, so render_naming and upload templates stay aligned.

    Falls back to the original sanitized segment when the input doesn't
    match any known date format (e.g. a hand-edited folder name like
    `friday-night`). Better to render the original string than to fail
    a whole render with a parse error.
    """
    if not date:
        return "no-date"
    for fmt in _DATE_INPUT_FORMATS:
        try:
            parsed = datetime.strptime(date, fmt)
        except ValueError:
            continue
        return parsed.strftime("%Y.%m.%d")
    return safe_segment(date)


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


def tournament_json_path(team: str, tournament: str) -> Path:
    """Sidecar metadata file for a tournament (abbreviation, full_name, ...).

    Lives alongside the per-date subfolders inside the tournament dir.
    Missing file = use folder-name fallbacks for all fields.
    """
    return tournament_dir(team, tournament) / "tournament.json"


def renders_dir(team: str, tournament: str, date: str, match: str) -> Path:
    return match_dir(team, tournament, date, match) / "renders"


def youtube_sidecar_path(render_path: Path) -> Path:
    """YouTube-upload sidecar for a single rendered file.

    Convention: append `.youtube.json` to the full render filename so the
    pairing is unambiguous (e.g. `full_20260517_103045.mp4` ->
    `full_20260517_103045.mp4.youtube.json`). This survives the user
    renaming the .mp4 only when both files are renamed together - which
    is fine; the alternative (replacing the extension) collides if
    someone produces `foo.mp4` and `foo.mov`.
    """
    return render_path.with_name(render_path.name + ".youtube.json")


def highlights_dir(team: str, tournament: str, date: str, match: str) -> Path:
    """Root for per-player highlight clips: `<renders>/highlights/`.

    Each highlight is one file under `highlights/<team>/<NN>_<player>/`.
    Sub-folders keep many small clips browsable when a long match has
    dozens of highlights per player.
    """
    return renders_dir(team, tournament, date, match) / "highlights"


def focused_dir(team: str, tournament: str, date: str, match: str) -> Path:
    """Root for per-player focused clips: `<renders>/focused/`.

    Same shape as `highlights_dir` but bounded by FocusIn/FocusOut
    events instead of rally boundaries.
    """
    return renders_dir(team, tournament, date, match) / "focused"


def player_folder_name(jersey: int, display_name: str | None) -> str:
    """Build a per-player folder name: `<NN>_<name>` (or just `NN`).

    Jersey is zero-padded so folders sort by number on disk. `name` is
    sanitized for filesystem safety; an unknown player just gets the
    jersey number alone (no trailing underscore).
    """
    nn = f"{int(jersey):02d}"
    if not display_name:
        return nn
    safe = safe_segment(display_name)
    if not safe or safe == "unnamed":
        return nn
    return f"{nn}_{safe}"
