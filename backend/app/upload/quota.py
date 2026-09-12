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
* An insert that is **refused outright** (`uploadLimitExceeded`,
  `uploadRateLimitExceeded`) never creates a video, so `refund()` takes
  the charge back. Without it, two throttled attempts read as "3200
  units spent, 4 uploads left" when nothing whatsoever was uploaded -
  the number then describes our own retries instead of the channel.

What this ledger does NOT know
------------------------------
API units are only one of the two ceilings. YouTube separately caps how
many *videos* a channel may publish in a rolling window, and refuses
them with 400 `uploadLimitExceeded` regardless of how much quota is
left. That cap is invisible to the API - there is no endpoint that
reports it - so the only way to know is to be told, once, by a
rejection. `mark_upload_limit()` remembers that rejection so the UI can
stop advertising "N uploads left today" when the real answer is zero.

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


# How long to assume the channel's video-count cap stays shut after a
# `uploadLimitExceeded`. Google documents neither the limit nor its
# window; reports put it at a rolling 24h, and the cap clears
# gradually as older uploads age out. 6h is a compromise: long enough
# that we stop hammering, short enough that a cap which frees up in
# the afternoon isn't ignored until tomorrow. Retrying only costs a
# refused request - the upload bytes are never sent.
UPLOAD_LIMIT_COOLDOWN_SECONDS = 6 * 60 * 60.0


@dataclass
class QuotaState:
    day: str
    spent: int
    limit: int
    seconds_until_reset: int
    uploads_today: int
    events: list[dict[str, Any]]
    # Unix time until which YouTube is expected to keep refusing new
    # videos on this channel (0 = no known block). Survives the quota
    # day rollover on purpose: it is not a quota-day thing.
    upload_limit_until: float = 0.0

    @property
    def uploads_blocked(self) -> bool:
        return self.upload_limit_until > time.time()

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
            # a user actually plans around. Zero while the channel cap
            # is shut: quota we can't spend is not capacity, and saying
            # "4 uploads left" to someone whose uploads are all being
            # refused is worse than saying nothing.
            "uploads_remaining": (
                0
                if self.uploads_blocked
                else self.remaining // COSTS["videos.insert"]
            ),
            "upload_limit_until": self.upload_limit_until,
            "uploads_blocked": self.uploads_blocked,
        }


def _read() -> dict[str, Any]:
    p = _ledger_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"day": current_day(), "spent": 0, "events": []}
    if not isinstance(data, dict):
        return {"day": current_day(), "spent": 0, "events": []}
    # A stale day means the pool has refilled since we last wrote. The
    # channel's video-count cap is NOT a quota-day thing, so it carries
    # over the reset if it hasn't expired yet.
    if data.get("day") != current_day():
        fresh: dict[str, Any] = {"day": current_day(), "spent": 0, "events": []}
        until = float(data.get("upload_limit_until") or 0)
        if until > time.time():
            fresh["upload_limit_until"] = until
        return fresh
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


def refund(operation: str, units: Optional[int] = None, note: str = "") -> int:
    """Give back a charge for a call the API refused outright.

    Only for rejections that created nothing: `uploadLimitExceeded` and
    the rate-limit family. NOT for an upload that died halfway - Google
    bills the call, not the outcome, and those units are genuinely gone.

    Under-counting is the safe direction here: the API's own 403
    `quotaExceeded` is authoritative and parks the queue anyway, whereas
    over-counting makes the UI refuse uploads YouTube would have taken.
    """
    cost = units if units is not None else COSTS.get(operation, 1)
    with _lock:
        data = _read()
        data["spent"] = max(0, int(data.get("spent", 0)) - int(cost))
        events = list(data.get("events") or [])
        events.append(
            {
                "at": time.time(),
                "op": f"refund:{operation}",
                "units": -int(cost),
                "note": note,
            }
        )
        data["events"] = events[-_MAX_EVENTS:]
        _write(data)
        total = int(data["spent"])
    logger.info(
        "YouTube quota: -%d refunded for a refused %s (%d/%d today)",
        cost,
        operation,
        total,
        daily_limit(),
    )
    return total


def mark_upload_limit(
    seconds: float = UPLOAD_LIMIT_COOLDOWN_SECONDS, note: str = ""
) -> float:
    """Record that the channel's video-count cap is shut; returns the
    unix time it is expected to open again.

    There is no API to ask about this limit, so a rejection is the only
    evidence that will ever exist. Remembering it is what lets the queue
    stop starting uploads and the UI stop promising capacity.
    """
    until = time.time() + max(0.0, seconds)
    with _lock:
        data = _read()
        # Never shorten an existing cooldown: two rejections in a row
        # mean the cap is still shut, not that it reopened.
        data["upload_limit_until"] = max(
            float(data.get("upload_limit_until") or 0), until
        )
        events = list(data.get("events") or [])
        events.append(
            {
                "at": time.time(),
                "op": "uploadLimitExceeded",
                "units": 0,
                "note": note or "channel video-count limit reached",
            }
        )
        data["events"] = events[-_MAX_EVENTS:]
        _write(data)
        until = float(data["upload_limit_until"])
    logger.warning(
        "YouTube channel upload limit reached (%s); not attempting uploads "
        "for %.1f h",
        note,
        (until - time.time()) / 3600.0,
    )
    return until


def clear_upload_limit() -> None:
    """Forget the channel cap - after a successful upload proves it."""
    with _lock:
        data = _read()
        if not data.get("upload_limit_until"):
            return
        data["upload_limit_until"] = 0.0
        _write(data)
    logger.info("YouTube channel upload limit cleared by a successful upload")


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
        # Refunds cancel their insert: an attempt YouTube refused is not
        # an upload, and counting it makes "2 uploads today" appear on a
        # channel with nothing new on it.
        uploads_today=max(
            0,
            sum(1 for e in events if e.get("op") == "videos.insert")
            - sum(1 for e in events if e.get("op") == "refund:videos.insert"),
        ),
        upload_limit_until=float(data.get("upload_limit_until") or 0),
        events=events,
    )


def can_afford(operation: str, units: Optional[int] = None) -> bool:
    cost = units if units is not None else COSTS.get(operation, 1)
    return state().remaining >= cost


def format_wait(seconds: float) -> str:
    """"in 5h 12m" / "in 12m" - for messages that explain a wait."""
    secs = max(0, int(seconds))
    hours, rem = divmod(secs, 3600)
    minutes = rem // 60
    if hours:
        return f"in {hours}h {minutes:02d}m"
    return f"in {minutes}m"


def format_reset() -> str:
    """"in 5h 12m" - for messages that have to explain a wait."""
    secs = seconds_until_reset()
    hours, rem = divmod(secs, 3600)
    minutes = rem // 60
    if hours:
        return f"in {hours}h {minutes:02d}m"
    return f"in {minutes}m"
