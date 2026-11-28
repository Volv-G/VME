"""Clubs: the opponents' schools and clubs, set up before anyone plays them.

A club is a name, its colours and its badge - "NW Juniors", grey and
black, the crest - kept once and picked when a match is created or put
on the schedule, wherever that happens (VME, the site, the phone). The
match takes a copy: the opponent's name (which can then be made more
particular, "NW Jrs. Grey"), the colour chosen of the club's, and the
badge as the match's own `opponent_logo` file. Copied, not referenced,
like an opponent reused from an earlier match (`scanner.create_match`):
each match owns what is in its folder, and a club changed or deleted
later must not repaint matches already played.

On disk, beside the media tree:

    <clubs root>/<id>/club.json   {"name": ..., "colors": ["#rrggbb", ...], "logo": "logo.png",
                                   "hint": "Seattle nwjuniors.org"}

`hint` is what narrows an online search for the badge (club_lookup):
a city, a state, the website.
    <clubs root>/<id>/logo.<ext>

The id is made from the name when the club is created and kept when it
is renamed, so a schedule change left on the site that names a club
still finds it.
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..config import CLUBS_ROOT
from ..domain.match import Match
from . import paths

logger = logging.getLogger(__name__)

LOGO_STEM = "logo"
MAX_COLORS = 6
_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_HEX = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_lock = threading.Lock()


@dataclass
class Club:
    id: str
    name: str
    colors: list[str] = field(default_factory=list)
    logo: Optional[str] = None  # filename in the club's folder
    hint: str = ""  # narrows the online search for the badge

    @property
    def color(self) -> Optional[str]:
        """The club's main colour: the first."""
        return self.colors[0] if self.colors else None

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "colors": list(self.colors), "logo": self.logo, "hint": self.hint}


def normalize_color(value: Optional[str]) -> Optional[str]:
    """"#ABC" / "aabbcc" -> "#aabbcc"; "" / None -> None; else ValueError."""
    value = (value or "").strip()
    if not value:
        return None
    m = _HEX.match(value)
    if not m:
        raise ValueError(f"Not a colour (#rrggbb): {value!r}")
    hex_ = m.group(1).lower()
    if len(hex_) == 3:
        hex_ = "".join(c * 2 for c in hex_)
    return f"#{hex_}"


def _colors(values: Optional[list[str]]) -> list[str]:
    out: list[str] = []
    for v in values or []:
        c = normalize_color(v)
        if c and c not in out:
            out.append(c)
    if len(out) > MAX_COLORS:
        raise ValueError(f"At most {MAX_COLORS} colours")
    return out


def _name(value: Optional[str]) -> str:
    name = " ".join((value or "").split())
    if not name:
        raise ValueError("A club needs a name")
    if len(name) > 120:
        raise ValueError("Club name is too long")
    return name


def club_dir(club_id: str) -> Path:
    if not _ID.match(club_id or ""):
        raise LookupError(f"No club {club_id!r}")
    return CLUBS_ROOT / club_id


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")[:48].strip("-")
    return s or "club"


def _read(d: Path) -> Optional[Club]:
    try:
        data = json.loads((d / "club.json").read_text(encoding="utf-8"))
        return Club(
            id=d.name,
            name=str(data.get("name") or d.name),
            colors=[c for c in (data.get("colors") or []) if isinstance(c, str)],
            logo=data.get("logo") or None,
            hint=str(data.get("hint") or ""),
        )
    except FileNotFoundError:
        return None
    except Exception as exc:
        logger.warning("clubs: cannot read %s: %s", d, exc)
        return None


def _write(club: Club) -> None:
    d = club_dir(club.id)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "club.json.tmp"
    tmp.write_text(json.dumps(club.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(d / "club.json")


def list_clubs() -> list[Club]:
    """Every club, by name."""
    if not CLUBS_ROOT.is_dir():
        return []
    found = [c for d in CLUBS_ROOT.iterdir() if d.is_dir() and _ID.match(d.name) and (c := _read(d))]
    return sorted(found, key=lambda c: c.name.casefold())


def get(club_id: str) -> Club:
    club = _read(club_dir(club_id))
    if club is None:
        raise LookupError(f"No club {club_id!r}")
    return club


def _unique(name: str, club_id: Optional[str] = None) -> None:
    key = " ".join(name.casefold().split())
    if any(c.id != club_id and " ".join(c.name.casefold().split()) == key for c in list_clubs()):
        raise ValueError(f"There is already a club called {name}")


def _hint(value: Optional[str]) -> str:
    return " ".join((value or "").split())[:200]


def create(name: str, colors: Optional[list[str]] = None, hint: Optional[str] = None) -> Club:
    name = _name(name)
    cols = _colors(colors)
    with _lock:
        _unique(name)
        base = _slug(name)
        club_id, n = base, 2
        while (CLUBS_ROOT / club_id).exists():
            club_id, n = f"{base}-{n}", n + 1
        club = Club(id=club_id, name=name, colors=cols, hint=_hint(hint))
        _write(club)
    logger.info("clubs: created %s (%s)", club.id, club.name)
    return club


def update(club_id: str, *, name: Optional[str] = None, colors: Optional[list[str]] = None,
           hint: Optional[str] = None) -> Club:
    with _lock:
        club = get(club_id)
        if name is not None:
            club.name = _name(name)
            _unique(club.name, club_id)
        if colors is not None:
            club.colors = _colors(colors)
        if hint is not None:
            club.hint = _hint(hint)
        _write(club)
    return club


def delete(club_id: str) -> bool:
    d = club_dir(club_id)
    if not d.is_dir():
        return False
    with _lock:
        shutil.rmtree(d)
    logger.info("clubs: deleted %s", club_id)
    return True


def logo_file(club: Club) -> Optional[Path]:
    """The club's badge on disk, by its recorded name, else by the stem."""
    d = club_dir(club.id)
    if club.logo and (d / Path(club.logo).name).is_file():
        return d / Path(club.logo).name
    return paths.find_logo(d, LOGO_STEM)


def set_logo(club_id: str, filename: Optional[str]) -> Club:
    with _lock:
        club = get(club_id)
        club.logo = filename
        _write(club)
    return club


def find(typed: Optional[str]) -> Optional[Club]:
    """A club by id, or by its name typed (ignoring case and spacing)."""
    typed = (typed or "").strip()
    if not typed:
        return None
    if _ID.match(typed):
        club = _read(CLUBS_ROOT / typed)
        if club:
            return club
    key = " ".join(typed.casefold().split())
    return next((c for c in list_clubs() if " ".join(c.name.casefold().split()) == key), None)


def dress(folder: Path, m: Match, club: Club, color: Optional[str] = None) -> None:
    """Give the match in `folder` the club's colour and badge.

    `color` is the one picked (any colour, not only the club's); without
    one the club's main colour is taken, unless the match already has a
    colour of its own (from an opponent reused). The badge is copied in
    as the match's opponent logo, replacing any it had. The opponent's
    name on the roster is the match's, typed - "NW Jrs. Grey" - not the
    club's. Does not save the match.
    """
    picked = normalize_color(color)
    m.opponent_roster.team_name = m.opponent or m.opponent_roster.team_name or club.name
    m.opponent_roster.team_color = picked or m.opponent_roster.team_color or club.color
    src = logo_file(club)
    if src is None:
        return
    old = paths.find_logo(folder, paths.OPPONENT_LOGO_STEM)
    if old is not None:
        try:
            old.unlink()
        except OSError:
            logger.warning("clubs: could not remove %s", old)
    name = f"{paths.OPPONENT_LOGO_STEM}{src.suffix.lower()}"
    try:
        shutil.copyfile(src, folder / name)
        m.opponent_roster.team_logo_path = name
    except OSError:
        # The colour and name are most of it; a badge can be added later.
        logger.warning("clubs: could not copy %s into %s", src, folder)
