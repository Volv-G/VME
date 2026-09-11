"""Local ledger of YouTube Data API quota spend.

Why this exists
---------------
A default YouTube Data API project gets **10,000 units/day**, and
`videos.insert` costs **1,600** of them. That is six uploads per day -
and a single match day here produces a full render plus a reel per
player, i.e. 13 videos. The 7th upload fails with 403 `quotaExceeded`
*after* the file has been pushed over the wire, which on residential
upstream means a wasted hour.

So the queue needs to know the price list before it starts, not after.
This module keeps a small ledger of what we spent today, so the upload
job can ask "can I afford an insert?" and, if not, park itself until the
quota resets instead of burning bandwidth on a doomed request.

Quota facts this encodes
------------------------
* The daily pool resets at **midnight US/Pacific**, not local midnight
  and not UTC. Getting this wrong means we either refuse uploads the API
  would accept, or attempt ones it won't.
* Costs are fixed per method and published by Google; they are listed in
  `COSTS` with the ones we actually call.
* A request that *fails* with `quotaExceeded` costs nothing, but by then
  the pool is gone anyway - `mark_exhausted()` records that verdict as
  authoritative, because the API knows better than our arithmetic (other
  clients, retries, and our own restarts can all spend units we never
  saw).

The ledger is advisory. It can only under-count if something else uses
the same API project, which is why `mark_exhausted()` exists and why
nothing here ever *prevents* a manual retry.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from ..config import DATA_DIR

logger = logging.getLogger(__name__)

# Published unit costs for the methods we call.
# https://developers.google.com/youtube/v3/determine_quota_cost
COSTS: dict[str, int] = {
    "videos.insert": 1600,
    "videos.list": 1,
    "thumbnails.set": 50,
    "playlistItems.insert": 50,
}

# Default daily pool for an un-audited project. Override with
# VME_YT_DAILY_QUOTA after a successful quota increase - that is the one
# number that changes when Google approves an audit.
DEFAULT_DAILY_UNITS = 10_000

# Keep a small window of individual spends so the UI can explain where
# the day went ("4 uploads + 9 thumbnails") rather than just a total.
_MAX_EVENTS = 200

_lock = threading.Lock()


def _ledger_path() -> Path:
    return DATA_DIR / "youtube_quota.json"


def daily_limit() -> int:
    raw = os.environ.get("VME_YT_DAILY_QUOTA", "").strip()
    if raw:
        try:
            return max(0, int(raw))
        except ValueError:
            logger.warning("ignoring invalid VME_YT_DAILY_QUOTA=%r", raw)
    return DEFAULT_DAILY_UNITS


# ---- Pacific-day arithmetic -------------------------------------------------
#
# Google resets the pool at midnight US/Pacific. We avoid a tzdata
# dependency (Windows Python without `tzdata` installed has no zoneinfo
# database) by computing the offset from US daylight-saving rules, which
# have been stable since 2007: DST runs from the 2nd Sunday in March to
# the 1st Sunday in November.


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> datetime:
    """UTC-naive datetime of the `n`th `weekday` (Mon=0) of a month."""
    d = datetime(year, month, 1)
    delta = (weekday - d.weekday()) % 7
    return d + timedelta(days=delta + 7 * (n - 1))


def _pacific_offset_hours(utc: datetime) -> int:
    """-7 during PDT, -8 during PST, for a UTC-naive instant."""
    year = utc.year
    # Transitions happen at 2am local, i.e. 10:00/09:00 UTC.
    dst_start = _nth_weekday(year, 3, 6, 2) + timedelta(hours=10)  # 2nd Sun Mar
    dst_end = _nth_weekday(year, 11, 6, 1) + timedelta(hours=9)  # 1st Sun Nov
    return -7 if dst_start <= utc < dst_end else -8


def _pacific_now(ts: Optional[float] = None) -> datetime:
    utc = datetime.fromtimestamp(ts if ts is not None else time.time(), timezone.utc)
    naive = utc.replace(tzinfo=None)
    return naive + timedelta(hours=_pacific_offset_hours(naive))


def current_day(ts: Optional[float] = None) -> str:
    """The quota day (`YYYY-MM-DD` in US/Pacific) an instant belongs to."""
    return _pacific_now(ts).strftime("%Y-%m-%d")


def seconds_until_reset(ts: Optional[float] = None) -> int:
    """Seconds until the pool refills (next Pacific midnight)."""
    now = _pacific_now(ts)
    tomorrow = (now + timedelta(days=1)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return max(0, int((tomorrow - now).total_seconds()))


# ---- Ledger ----------------------------------------------------------------


@dataclass
class QuotaState:
    day: str
    spent: int
    limit: int
    seconds_until_reset: int
    uploads_today: int
    events: list[dict[str, Any]]

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.spent)

    def to_dict(self) -> dict[str, Any]:
        return {
            "day": self.day,
            "spent": self.spent,
            "limit": self.limit,
            "remaining": self.remaining,
            "seconds_until_reset": self.seconds_until_reset,
            "uploads_today": self.uploads_today,
            # How many more videos fit in what's left - the only number
            # a user actually plans around.
            "uploads_remaining": self.remaining // COSTS["videos.insert"],
        }


def _read() -> dict[str, Any]:
    p = _ledger_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"day": current_day(), "spent": 0, "events": []}
    if not isinstance(data, dict):
        return {"day": current_day(), "spent": 0, "events": []}
    # A stale day means the pool has refilled since we last wrote.
    if data.get("day") != current_day():
        return {"day": current_day(), "spent": 0, "events": []}
    data.setdefault("spent", 0)
    data.setdefault("events", [])
    return data


def _write(data: dict[str, Any]) -> None:
    p = _ledger_path()
    tmp = p.with_suffix(".tmp")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.replace(tmp, p)
    except OSError as exc:
        # Never let bookkeeping break an upload.
        logger.warning("could not write the YouTube quota ledger: %s", exc)


def record(operation: str, units: Optional[int] = None, note: str = "") -> int:
    """Charge `operation` to today's ledger; returns the new total.

    Unknown operations are charged 1 unit (the API's floor) so a new call
    site can't silently spend nothing.
    """
    cost = units if units is not None else COSTS.get(operation, 1)
    with _lock:
        data = _read()
        data["spent"] = int(data.get("spent", 0)) + int(cost)
        events = list(data.get("events") or [])
        events.append(
            {"at": time.time(), "op": operation, "units": cost, "note": note}
        )
        data["events"] = events[-_MAX_EVENTS:]
        _write(data)
        total = int(data["spent"])
    logger.info(
        "YouTube quota: +%d for %s (%d/%d today)",
        cost,
        operation,
        total,
        daily_limit(),
    )
    return total


def mark_exhausted(note: str = "") -> None:
    """Record the API's own verdict that the pool is gone.

    Called when a request comes back 403 `quotaExceeded`. Trust it over
    our arithmetic: another client, a retry, or spend from before this
    ledger existed can all put us over without us noticing.
    """
    with _lock:
        data = _read()
        data["spent"] = max(int(data.get("spent", 0)), daily_limit())
        events = list(data.get("events") or [])
        events.append(
            {
                "at": time.time(),
                "op": "quotaExceeded",
                "units": 0,
                "note": note or "API reported the daily quota is exhausted",
            }
        )
        data["events"] = events[-_MAX_EVENTS:]
        _write(data)
    logger.warning("YouTube quota exhausted for %s (%s)", current_day(), note)


def state() -> QuotaState:
    with _lock:
        data = _read()
    events = list(data.get("events") or [])
    return QuotaState(
        day=str(data.get("day") or current_day()),
        spent=int(data.get("spent") or 0),
        limit=daily_limit(),
        seconds_until_reset=seconds_until_reset(),
        uploads_today=sum(1 for e in events if e.get("op") == "videos.insert"),
        events=events,
    )


def can_afford(operation: str, units: Optional[int] = None) -> bool:
    cost = units if units is not None else COSTS.get(operation, 1)
    return state().remaining >= cost


def format_reset() -> str:
    """"in 5h 12m" - for messages that have to explain a wait."""
    secs = seconds_until_reset()
    hours, rem = divmod(secs, 3600)
    minutes = rem // 60
    if hours:
        return f"in {hours}h {minutes:02d}m"
    return f"in {minutes}m"
