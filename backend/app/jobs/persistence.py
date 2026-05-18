"""Persist render-job state to disk so queue history survives restarts.

A single JSON file (`<DATA_DIR>/jobs.json`) holds every known job plus the
queue's active flag. We rewrite the whole file on every state change -
the file is tiny and atomic-replace is plenty cheap at this scale.

Restart semantics:
  - PENDING jobs are preserved as pending; the dispatcher will pick them
    up on next `Start queue`.
  - RUNNING jobs at shutdown are flipped to FAILED at load time
    (renderer can't resume mid-frame).
  - Terminal jobs (DONE/FAILED/CANCELLED) are kept as history.
  - We auto-prune terminal jobs beyond a cap so the file doesn't grow
    unboundedly across years of use.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from pathlib import Path
from typing import Optional

from ..config import DATA_DIR, PROJECT_ROOT
from .manager import JOBS, JobStatus, RenderJob

logger = logging.getLogger(__name__)


# Total terminal-job history kept on disk. Older terminal jobs get pruned
# (oldest first) on each save. 200 leaves plenty of room while preventing
# accidental unbounded growth.
MAX_TERMINAL_HISTORY = 200

# Serializes writes from multiple threads (HTTP handlers + dispatcher).
# Without this, two near-simultaneous saves can both try to atomically
# rename their temp file onto `jobs.json`; Windows in particular fails
# the second rename with EACCES because of the brief moment one process
# holds the destination handle.
_SAVE_LOCK = threading.Lock()


def _state_file() -> Path:
    """Where the queue state lives on disk.

    Pinned to `DATA_DIR/jobs.json` when `VME_DATA_DIR` is set; otherwise
    falls back to the project root for legacy repo-local installs.
    """
    base = DATA_DIR if DATA_DIR is not None else PROJECT_ROOT
    return Path(base) / "jobs.json"


def load() -> None:
    """Read the state file and hydrate the global JobManager.

    Silent no-op if the file doesn't exist yet (first boot). Corrupt or
    unreadable files are logged but don't prevent startup - we just start
    with an empty queue.
    """
    path = _state_file()
    if not path.is_file():
        logger.info("no persisted queue at %s; starting empty", path)
        JOBS.load_from([], active=False)
        return
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        logger.warning("failed to read %s: %s; starting empty", path, exc)
        JOBS.load_from([], active=False)
        return

    raw_jobs = data.get("jobs", [])
    jobs: list[RenderJob] = []
    for raw in raw_jobs:
        try:
            jobs.append(RenderJob.from_dict(raw))
        except (KeyError, ValueError, TypeError) as exc:
            logger.warning("skipping malformed job entry: %s (%s)", raw, exc)
    # Active flag is intentionally ignored on load: queue starts paused
    # after every restart so the user has explicit control. The field is
    # still written for forward-compat / debugging.
    JOBS.load_from(jobs, active=False)


def save() -> None:
    """Write the current state to disk atomically.

    Uses NamedTemporaryFile + os.replace so a crash mid-write can't
    corrupt the existing file. Pruning happens here (not on every job
    transition) so the cap is enforced uniformly. The whole sequence is
    guarded by `_SAVE_LOCK` because both the dispatcher thread and the
    request-handling threads can drive state changes concurrently.
    """
    with _SAVE_LOCK:
        path = _state_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        jobs = list(JOBS.list_jobs())
        jobs = _prune(jobs)
        payload = {
            "active": JOBS.is_active(),
            "jobs": [j.to_dict() for j in jobs],
        }
        try:
            tmp_fd, tmp_path = tempfile.mkstemp(
                dir=str(path.parent), prefix=".jobs-", suffix=".json"
            )
            try:
                with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                    json.dump(payload, f, indent=2)
                os.replace(tmp_path, path)
            except Exception:
                # Clean up the temp file on error so we don't litter the
                # data dir with .jobs-XXXX.json fragments.
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass
                raise
        except OSError as exc:
            logger.warning("failed to persist queue state to %s: %s", path, exc)


def install_hook() -> None:
    """Wire `JobManager.on_state_changed` so every change flushes to disk."""
    JOBS.on_state_changed = save


# ---- helpers ---------------------------------------------------------------


_TERMINAL = {JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED}


def _prune(jobs: list[RenderJob]) -> list[RenderJob]:
    """Drop the oldest terminal jobs once `MAX_TERMINAL_HISTORY` is exceeded.

    Live jobs (pending / running) are never pruned regardless of count -
    they represent work the user explicitly asked for.
    """
    terminal = [j for j in jobs if j.status in _TERMINAL]
    if len(terminal) <= MAX_TERMINAL_HISTORY:
        return jobs
    # Oldest first by finished_at (fallback to created_at).
    terminal.sort(key=lambda j: j.finished_at or j.created_at)
    to_drop = set(j.id for j in terminal[: len(terminal) - MAX_TERMINAL_HISTORY])
    if to_drop:
        logger.info("pruning %d old terminal job(s) from queue history", len(to_drop))
    return [j for j in jobs if j.id not in to_drop]
