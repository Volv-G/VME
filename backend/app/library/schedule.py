"""Matches on the schedule: added before they are played, with a start time.

A scheduled match is an ordinary match folder - `<Tournament>/<date>/<NN>_<opponent>/`
with a match.json - that has no footage and no events yet, plus
`Match.start_time`. Nothing else is needed: once the camera files land in
it, it is simply a match.

Three places can put a match on the schedule, and VME stays the one
that keeps it:

- VME itself (the new-match form takes a time).
- The site, by a coach or the admin, and the phone at the gym. Neither
  can reach this PC, so they leave a change with the site and VME
  fetches it on its next sync (publish/site_sync.py) and applies it
  here - `apply_edit` - then republishes, which is when everyone sees it.

Only a match that hasn't been played can be moved or taken off the
schedule from outside: one with footage or events is history, and a
stray tap on a phone must not touch it.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from typing import Any, Optional

from ..domain.match import Match
from ..domain.tournament import Tournament
from . import paths, scanner

logger = logging.getLogger(__name__)

_TIME = re.compile(r"^(\d{1,2}):(\d{2})$")


def normalize_time(value: Optional[str]) -> Optional[str]:
    """"6:30" -> "06:30"; "" / None -> None; anything else is refused."""
    value = (value or "").strip()
    if not value:
        return None
    m = _TIME.match(value)
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ValueError(f"Not a time of day (HH:MM): {value!r}")
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def normalize_date(value: Optional[str]) -> str:
    value = (value or "").strip()
    try:
        return dt.date.fromisoformat(value).isoformat()
    except ValueError:
        raise ValueError(f"Not a date (YYYY-MM-DD): {value!r}") from None


def upcoming(team: str, *, today: Optional[dt.date] = None) -> list[dict[str, Any]]:
    """Matches not played yet, from today on, soonest first."""
    today = today or dt.date.today()
    out = []
    for d in paths.iter_tournament_dirs(team):
        info = scanner.load_tournament_info(team, d.name)
        name = info.resolve_full_name(d.name.replace("_", " "))
        for s in scanner.list_matches(team, d.name):
            if s.played or s.date < today.isoformat():
                continue
            out.append({
                "key": f"{d.name}/{s.date}/{s.name}",
                "tournament": d.name,
                "tournament_name": name,
                "date": s.date,
                "time": s.start_time,
                "index": s.match_index or None,
                "opponent": s.opponent or paths.parse_match_folder(s.name)[1].replace("_", " "),
            })
    out.sort(key=lambda m: (m["date"], m["time"] or "99:99", m["index"] or 0))
    return out


def _tournament(team: str, typed: str) -> str:
    """The tournament folder for a name typed on the site or the phone:
    an existing one when it matches by folder or display name (ignoring
    case and spacing), else a new folder that remembers the typed name."""
    typed = (typed or "").strip()
    if not typed:
        raise ValueError("Tournament is required")

    def key(s: str) -> str:
        return " ".join(s.replace("_", " ").casefold().split())

    for d in paths.iter_tournament_dirs(team):
        info = scanner.load_tournament_info(team, d.name)
        if key(typed) in {key(d.name), key(info.full_name or ""), key(info.abbreviation or "")}:
            return d.name
    folder = paths.safe_segment(typed)
    scanner.create_tournament(team, folder)
    if folder != typed:
        Tournament(full_name=typed).save(paths.tournament_json_path(team, folder))
    return folder


def _load(team: str, key: str) -> tuple[tuple[str, str, str], Match]:
    parts = (key or "").split("/")
    if len(parts) != 3 or not all(parts):
        raise ValueError(f"Not a match key: {key!r}")
    path = paths.match_json_path(team, *parts)
    if not path.is_file():
        raise LookupError(f"No match {key}")
    return (parts[0], parts[1], parts[2]), Match.load(path)


def _played(team: str, where: tuple[str, str, str], m: Match) -> bool:
    return bool(m.events or m.clips or scanner.match_video(paths.match_dir(team, *where)))


def add(team: str, tournament: str, date: str, opponent: str, time: Optional[str]) -> str:
    """Put a match on the schedule; returns its key."""
    opponent = (opponent or "").strip()
    if not opponent:
        raise ValueError("Opponent is required")
    date = normalize_date(date)
    time = normalize_time(time)
    folder = _tournament(team, tournament)
    name, m = scanner.create_match(team, folder, date, opponent)
    m.start_time = time
    m.save(paths.match_json_path(team, folder, date, name))
    return f"{folder}/{date}/{name}"


def update(team: str, key: str, *, date: Optional[str], time: Optional[str], opponent: Optional[str]) -> str:
    """Change an unplayed match's day, time or opponent; returns its key
    (a new day or opponent means a new folder, so a new key)."""
    where, m = _load(team, key)
    if _played(team, where, m):
        raise ValueError(f"{key} has been played; change it in VME")
    new_date = normalize_date(date) if date else where[1]
    new_opponent = (opponent or "").strip() or m.opponent
    if new_date != where[1] or new_opponent != m.opponent:
        # Recreate rather than rename: the folder name carries the date
        # and the opponent, and create_match picks a free match number
        # on the new day. The opponent's roster and badge come along.
        new_name, fresh = scanner.create_match(
            team, where[0], new_date, new_opponent,
            opponent_from=where if new_opponent == m.opponent else None,
        )
        fresh.start_time = normalize_time(time)
        fresh.save(paths.match_json_path(team, where[0], new_date, new_name))
        scanner.delete_match(team, *where)
        return f"{where[0]}/{new_date}/{new_name}"
    m.start_time = normalize_time(time)
    m.save(paths.match_json_path(team, *where))
    return key


def remove(team: str, key: str) -> None:
    """Take an unplayed match off the schedule."""
    where, m = _load(team, key)
    if _played(team, where, m):
        raise ValueError(f"{key} has been played; it can't be taken off the schedule")
    scanner.delete_match(team, *where)


def apply_edit(edit: dict) -> bool:
    """Apply one change left on the site. True = done, or never can be
    (the site may forget it); False = try again on the next sync."""
    team = str(edit.get("team") or "")
    if team not in {t.name for t in scanner.list_teams()}:
        logger.warning("schedule change for unknown team %r dropped", team)
        return True
    action = edit.get("action")
    try:
        if action == "add":
            key = add(team, edit.get("tournament") or "", edit.get("date") or "",
                      edit.get("opponent") or "", edit.get("time"))
        elif action == "update":
            key = update(team, edit.get("key") or "", date=edit.get("date"),
                         time=edit.get("time"), opponent=edit.get("opponent"))
        elif action == "delete":
            remove(team, edit.get("key") or "")
            key = edit.get("key")
        else:
            logger.warning("schedule change with unknown action %r dropped", action)
            return True
    except (ValueError, LookupError, scanner.MatchHasClips) as exc:
        logger.warning("schedule change %s for %s dropped: %s", action, team, exc)
        return True
    logger.info("schedule: %s %s/%s (by %s)", action, team, key, edit.get("submitted_by", "?"))
    return True
