"""Publish what families may see to the public VME site (`site/`).

VME stays the source of truth and the site holds a copy: this builds the
documents described in `site/shared/docs.ts` - teams, each team's season,
each match - and replaces the site's copy in one request. Nothing on the
internet ever calls into VME; the site works with this PC switched off.

What goes out, and what deliberately does not:

- Players by short name ("Kate G"), never the full name on the roster,
  with their photo shrunk to a thumbnail.
- Scores, set by set, and the same per-player stats VME shows.
- Videos that are on YouTube and not private - a private video would
  only show an error to everyone but the channel owner.
- No footage, renders or files of any kind: the site embeds YouTube.

Configured by `<data dir>/site.json`: `{"url": ..., "token": ...}`, plus
optionally `"teams": [...]` to publish only some teams.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Optional

import requests

from ..config import DATA_DIR, MEDIA_ROOT
from ..domain.match import Match
from ..domain.roster import Roster
from ..domain.stats import Stats
from ..library import paths, scanner

logger = logging.getLogger(__name__)

PHOTO_PX = 256
LOGO_PX = 160


def _config_path() -> Path:
    return (DATA_DIR or MEDIA_ROOT.parent) / "site.json"


def load_config() -> Optional[dict]:
    try:
        cfg = json.loads(_config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return cfg if cfg.get("url") and cfg.get("token") else None


def _short_name(p) -> str:
    """"Kate G": the roster's short name, else first name + initial."""
    if getattr(p, "short_name", None):
        return p.short_name
    parts = (p.name or "").split()
    if len(parts) >= 2:
        return f"{parts[0]} {parts[-1][0]}"
    return p.name or ""


def _image(path: Optional[str | Path], px: int, *, crop: bool) -> Optional[dict]:
    """A file shrunk to `px` and re-encoded, as a publishable image."""
    if not path:
        return None
    try:
        from PIL import Image

        with Image.open(path) as im:
            if crop:
                # A square face crop, biased upward like the thumbnails.
                w, h = im.size
                side = min(w, h)
                top = max(0, int((h - side) * 0.22))
                im = im.crop(((w - side) // 2, top, (w - side) // 2 + side, top + side))
            im.thumbnail((px, px))
            buf = io.BytesIO()
            if crop:
                im.convert("RGB").save(buf, "JPEG", quality=85)
                mime = "image/jpeg"
            else:
                # Logos keep their transparency.
                im.convert("RGBA").save(buf, "PNG", optimize=True)
                mime = "image/png"
        return {"mime": mime, "base64": base64.b64encode(buf.getvalue()).decode("ascii")}
    except Exception:
        logger.warning("site publish: could not read image %s", path, exc_info=True)
        return None


def _stats_out(stats: Stats, names: dict[int, str]) -> dict:
    from ..api.stats import _out

    out = _out(Roster(), stats).model_dump()
    for line in out["players"]:
        line["name"] = names.get(line["number"], "") or f"#{line['number']}"
    out["total"]["name"] = "Team"
    return out


def _final_state(m: Match):
    state = None
    for e in m.events:
        if e.state is not None:
            state = e.state
    return state or m.initial_state()


def _videos(team: str) -> dict[str, list[dict]]:
    """`<tournament>/<date>/<match>` -> the match's YouTube videos."""
    out: dict[str, list[dict]] = {}
    for r in scanner.list_team_full_renders(team):
        if not r.youtube_video_id or (r.youtube_privacy_status or "") == "private":
            continue
        v: dict[str, Any] = {"kind": r.kind or "full", "youtube_id": r.youtube_video_id}
        if r.kind == "reel":
            parts = Path(r.filename).parts
            jersey = paths.parse_player_folder(parts[2])[0] if len(parts) > 2 else None
            v["player_number"] = jersey
            v["player_label"] = r.player_label or ""
        out.setdefault(f"{r.tournament}/{r.date}/{r.match}", []).append(v)
    order = {"full": 0, "condensed": 1, "reel": 2}
    for vids in out.values():
        vids.sort(key=lambda v: (order.get(v["kind"], 3), v.get("player_number") or 0))
    return out


def build(team: str) -> tuple[dict, list[dict], list[dict]]:
    """(team doc, docs, images) for one team. Docs include the team doc."""
    from ..api.stats import _add_match
    from ..jobs.render_job import _resolve_logo, _resolve_player_photo

    roster = scanner.load_team_roster(team)
    names = {p.number: _short_name(p) for p in roster.players}
    now = int(time.time())
    images: list[dict] = []
    docs: list[dict] = []

    def add_image(key: str, img: Optional[dict]) -> Optional[str]:
        if img is None:
            return None
        images.append({"key": key, **img})
        return key

    team_name = roster.team_name or team.replace("_", " ")
    color = roster.team_color or "#2d8a4e"
    logo = add_image(
        f"img/{team}/logo",
        _image(_resolve_logo(paths.team_dir(team), roster.team_logo_path, paths.TEAM_LOGO_STEM), LOGO_PX, crop=False),
    )
    players = [
        {
            "number": p.number,
            "name": names[p.number],
            "photo": add_image(
                f"img/{team}/player/{p.number}",
                _image(_resolve_player_photo(team, roster, p.number), PHOTO_PX, crop=True),
            ),
        }
        for p in sorted(roster.players, key=lambda p: p.number)
    ]

    videos = _videos(team)
    season = Stats()
    tournaments = []
    for d in paths.iter_tournament_dirs(team):
        info = scanner.load_tournament_info(team, d.name)
        t_name = info.full_name or info.abbreviation or d.name.replace("_", " ")
        matches = []
        for s in scanner.list_matches(team, d.name):
            if not s.has_match_json:
                continue
            path = paths.match_json_path(team, d.name, s.date, s.name)
            try:
                m = Match.load(path)
            except Exception as exc:
                logger.warning("site publish: skipping %s: %s", path, exc)
                continue
            state = _final_state(m)
            one = Stats()
            _add_match(one, path, roster)
            season.add(one)
            key = f"{d.name}/{s.date}/{s.name}"
            result = None
            if state.game_ended and state.home_sets != state.away_sets:
                result = "W" if state.home_sets > state.away_sets else "L"
            folder = paths.match_dir(team, d.name, s.date, s.name)
            summary = {
                "key": key,
                "date": s.date,
                "index": s.match_index or None,
                "opponent": m.opponent_roster.team_name or s.opponent,
                "opponent_logo": add_image(
                    f"img/{team}/opponent/{key}",
                    _image(
                        _resolve_logo(folder, m.opponent_roster.team_logo_path, paths.OPPONENT_LOGO_STEM),
                        LOGO_PX,
                        crop=False,
                    ),
                ),
                "sets": [list(x) for x in state.set_scores],
                "home_sets": state.home_sets,
                "away_sets": state.away_sets,
                "result": result,
                "videos": videos.get(key, []),
            }
            matches.append(summary)
            docs.append({
                "key": f"match/{team}/{key}",
                "json": {
                    **summary,
                    "team": team,
                    "team_name": team_name,
                    "tournament": d.name,
                    "tournament_name": t_name,
                    "stats": _stats_out(one, names),
                    "published_at": now,
                },
            })
        if matches:
            tournaments.append({"slug": d.name, "name": t_name, "matches": matches})
    # Latest tournament first; matches inside stay in playing order.
    tournaments.sort(key=lambda t: max(m["date"] for m in t["matches"]), reverse=True)

    team_doc = {
        "slug": team,
        "name": team_name,
        "color": color,
        "logo": logo,
        "players": players,
        "stats": _stats_out(season, names),
        "tournaments": tournaments,
        "season_reels": [],
        "published_at": now,
    }
    docs.append({"key": f"team/{team}", "json": team_doc})
    return team_doc, docs, images


def publish(teams: Optional[list[str]] = None) -> dict:
    """Replace everything on the site with the current state of `teams`
    (default: the config's list, else every team). Returns counts."""
    cfg = load_config()
    if cfg is None:
        raise RuntimeError(f"Site publishing is not configured ({_config_path()} missing).")
    teams = teams or cfg.get("teams") or [t.name for t in scanner.list_teams()]
    docs: list[dict] = []
    images: list[dict] = []
    listing = []
    for team in teams:
        team_doc, team_docs, team_images = build(team)
        docs += team_docs
        images += team_images
        listing.append({k: team_doc[k] for k in ("slug", "name", "color", "logo")})
    docs.append({"key": "teams", "json": {"teams": listing, "published_at": int(time.time())}})
    body = {
        # Everything is replaced in one transaction, so a match deleted in
        # VME disappears from the site instead of lingering.
        "replace_prefixes": ["team/", "match/", "img/"],
        "docs": docs,
        "images": images,
    }
    r = requests.post(
        cfg["url"].rstrip("/") + "/api/publish",
        json=body,
        headers={"Authorization": f"Bearer {cfg['token']}"},
        timeout=120,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Site refused the publish: {r.status_code} {r.text[:300]}")
    logger.info("site publish: %d docs, %d images for %s", len(docs), len(images), ", ".join(teams))
    return {"teams": teams, "docs": len(docs), "images": len(images), "url": cfg["url"]}


_timer_lock = threading.Lock()
_timer: Optional[threading.Timer] = None


def publish_soon(delay: float = 60.0) -> None:
    """Publish once things settle: a match day finishes a dozen uploads in
    a row, and one publish after the last is all the site needs."""
    global _timer
    if load_config() is None:
        return

    def run() -> None:
        try:
            publish()
        except Exception:
            logger.warning("site publish failed", exc_info=True)

    with _timer_lock:
        if _timer is not None:
            _timer.cancel()
        _timer = threading.Timer(delay, run)
        _timer.daemon = True
        _timer.start()
