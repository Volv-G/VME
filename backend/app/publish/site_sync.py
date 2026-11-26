"""Keep VME and the site in step - every call made from here, never to here.

Once a minute VME asks the site (`POST /api/publish/sync`) for:

- **requests**: people asking to be a team's coach or one of its
  players. VME shows them so the admin can approve or decline without
  opening the site; decisions ride along on the next call.
- **edits**: a player's new name or photo, made on the site by a parent
  (or the admin). VME writes them into the team roster, tells the site
  they're applied (the site then forgets them) and republishes, which
  is when the site starts showing them.
- **schedule**: matches added, moved or taken off the schedule on the
  site or from the phone (library/schedule.py), applied and acked the
  same way.

The site keeps working with this PC off; edits and requests simply wait.
"""

from __future__ import annotations

import base64
import io
import logging
import threading
import time
from dataclasses import replace
from typing import Any, Optional

import requests

from ..library import paths, scanner, schedule
from . import site

logger = logging.getLogger(__name__)

INTERVAL = 60.0
FIRST_DELAY = 20.0


class SiteSync:
    """The polling thread, plus the latest list of requests it saw."""

    def __init__(self) -> None:
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        # One sync at a time: the thread and a decision made in VME's UI.
        self._lock = threading.Lock()
        self.requests: list[dict] = []
        self.last_sync: Optional[float] = None
        self.error: Optional[str] = None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="site-sync", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()

    def _loop(self) -> None:
        self._wake.wait(FIRST_DELAY)
        while not self._stop.is_set():
            self._wake.clear()
            if site.load_config() is not None:
                try:
                    self.sync()
                except Exception as exc:  # network down, site redeploying...
                    self.error = str(exc)
                    logger.info("site sync failed: %s", exc)
            self._wake.wait(INTERVAL)

    # -- one round ---------------------------------------------------------

    def sync(self, decide: Optional[list[dict]] = None) -> list[dict]:
        """Send `decide` (if any), apply new edits, return pending requests."""
        cfg = site.load_config()
        if cfg is None:
            raise RuntimeError("Site publishing is not configured.")
        with self._lock:
            data = _call(cfg, {"decide": decide or []})
            applied = [e["id"] for e in data.get("edits", []) if _apply_edit(e)]
            scheduled = [e["id"] for e in data.get("schedule", []) if schedule.apply_edit(e)]
            if scheduled:
                # Publish before the ack: until then the site shows the
                # change as pending, so it never vanishes in between.
                try:
                    site.publish()
                except Exception:
                    logger.warning("publish after schedule changes failed", exc_info=True)
            if applied or scheduled:
                data = _call(cfg, {"ack": applied, "schedule_ack": scheduled})
                site.publish_soon(5.0)
            self.requests = data.get("requests", [])
            self.last_sync = time.time()
            self.error = None
            return self.requests


def _call(cfg: dict, body: dict) -> dict[str, Any]:
    r = requests.post(
        cfg["url"].rstrip("/") + "/api/publish/sync",
        json=body,
        headers={"Authorization": f"Bearer {cfg['token']}"},
        timeout=60,
    )
    if r.status_code != 200:
        raise RuntimeError(f"Site sync refused: {r.status_code} {r.text[:200]}")
    return r.json()


def _apply_edit(edit: dict) -> bool:
    """Write one site edit into the roster. True = done (or never can be),
    so the site may forget it; False = try again next round."""
    team = str(edit.get("team") or "")
    try:
        number = int(edit.get("number"))
    except (TypeError, ValueError):
        return True
    if team not in {t.name for t in scanner.list_teams()}:
        logger.warning("site edit for unknown team %r dropped", team)
        return True
    roster = scanner.load_team_roster(team)
    if roster.find_by_number(number) is None:
        logger.warning("site edit for %s #%d dropped: not on the roster", team, number)
        return True

    changes: dict[str, Any] = {}
    name = (edit.get("name") or "").strip()
    if name:
        changes["name"] = name
    if edit.get("short_name") is not None:
        changes["short_name"] = str(edit["short_name"]).strip() or None
    if edit.get("photo_base64"):
        try:
            changes["profile_pic_path"] = _save_photo(team, number, base64.b64decode(edit["photo_base64"]))
        except Exception:
            logger.warning("site edit photo for %s #%d unreadable; dropped", team, number, exc_info=True)
    if changes:
        roster.players = [replace(p, **changes) if p.number == number else p for p in roster.players]
        scanner.save_team_roster(team, roster)
        logger.info(
            "site edit applied to %s #%d by %s: %s",
            team, number, edit.get("submitted_by", "?"), ", ".join(sorted(changes)),
        )
    return True


def _save_photo(team: str, number: int, data: bytes) -> str:
    """Store as `players/<NN>.jpg`, replacing any earlier photo; return
    the roster's relative path. Re-encoded so only a real image lands."""
    from PIL import Image

    with Image.open(io.BytesIO(data)) as im:
        im = im.convert("RGB")
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=92)
    directory = paths.player_photo_dir(team)
    directory.mkdir(parents=True, exist_ok=True)
    stem = paths.player_photo_stem(number)
    previous = paths.find_logo(directory, stem)
    if previous is not None:
        previous.unlink()
    target = directory / f"{stem}.jpg"
    target.write_bytes(buf.getvalue())
    return f"{paths.PLAYER_PHOTO_SUBDIR}/{target.name}"


WORKER = SiteSync()
