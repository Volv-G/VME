"""In-memory render job registry + global queue state.

Jobs no longer auto-run on creation. `JobManager.enqueue()` adds a `pending`
job; a separate dispatcher thread (see `dispatcher.py`) pulls jobs FIFO when
the global queue is active. This lets the user line up several renders
across matches and start them all at once from the team dashboard - the
match editor stays responsive while editing.

The manager owns:
  - the job registry (id -> RenderJob)
  - the global `active` flag (queue running or paused)
  - a `threading.Event` the dispatcher waits on
  - per-job SSE listener queues
  - a `state_changed` hook invoked on every state change so the
    persistence layer can write to disk without coupling here.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


# Non-terminal statuses (the job is either queued or being worked on).
_LIVE_STATUSES = {JobStatus.PENDING, JobStatus.RUNNING}
_TERMINAL_STATUSES = {JobStatus.DONE, JobStatus.FAILED, JobStatus.CANCELLED}


@dataclass
class RenderJob:
    id: str
    team: str
    tournament: str
    date: str
    match: str
    label: str = ""
    # Kind of job this represents. Drives dispatch in render_job.py:
    #   - "full"               -> whole match, one output
    #   - "preview"            -> window around `playhead_frame`, one output
    #   - "highlights"         -> one mp4 per HighlightEvent (rally bounds)
    #   - "focused_highlights" -> one mp4 per FocusIn/FocusOut pair
    #   - "player_reels"       -> one file per player: every play of theirs
    #                             concatenated + a `.chapters.txt` sidecar.
    #                             Separate from "highlights", not a
    #                             replacement - both can run on a match.
    #   - "youtube_upload"     -> upload an existing render file to YouTube
    #                             (not a render at all; reuses the queue's
    #                             FIFO + progress + SSE plumbing).
    #   - "media_server_copy"  -> copy a finished render + its Jellyfin
    #                             images into the team's media-server
    #                             folder, renamed for a library.
    # Stored as a plain string for forward compatibility (older clients
    # just don't know about new kinds; the backend still serializes them).
    kind: str = "full"
    # Render parameters captured at enqueue time so the dispatcher can run
    # the job later without the original HTTP context.
    playhead_frame: Optional[int] = None
    seconds_around: Optional[float] = None
    # Display helpers stored on the job so the queue UI doesn't need to
    # cross-reference match.json files just to render a row.
    opponent: str = ""
    match_index: Optional[int] = None
    # Immediate jobs (e.g. previews) bypass the dispatcher and run in
    # their own thread the moment they're enqueued. They still appear in
    # the jobs registry / SSE / persistence so the UI shows progress; the
    # dispatcher's `next_pending()` filters them out so it never races to
    # double-run the same job.
    immediate: bool = False
    # Free-form per-kind payload. Avoids cluttering this dataclass with
    # fields that only one kind uses. Currently carries:
    #   - youtube_upload: {filename, title, description, privacy_status,
    #                      playlist_id, tags}
    #   - media_server_copy: {filename, target, images, skip_if_current}
    payload: dict[str, Any] = field(default_factory=dict)

    status: JobStatus = JobStatus.PENDING
    percent: float = 0.0
    phase: str = "queued"
    message: str = ""
    output_filename: Optional[str] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    # Worker thread polls this; set() asks the renderer to abort at the
    # next frame boundary. For pending jobs, the dispatcher checks it
    # before starting and marks the job cancelled without ever running.
    # Not serialized.
    cancel_event: threading.Event = field(
        default_factory=threading.Event, repr=False, compare=False
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "team": self.team,
            "tournament": self.tournament,
            "date": self.date,
            "match": self.match,
            "label": self.label,
            "kind": self.kind,
            "playhead_frame": self.playhead_frame,
            "seconds_around": self.seconds_around,
            "opponent": self.opponent,
            "match_index": self.match_index,
            "immediate": self.immediate,
            "payload": self.payload,
            "status": self.status.value,
            "percent": round(self.percent, 1),
            "phase": self.phase,
            "message": self.message,
            "output_filename": self.output_filename,
            "error": self.error,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "cancel_requested": self.cancel_event.is_set(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RenderJob":
        """Reconstruct a job from a persisted dict.

        The transient `cancel_event` is recreated fresh - any cancel state
        captured at the previous shutdown is irrelevant once the process
        restarts.
        """
        job = cls(
            id=data["id"],
            team=data["team"],
            tournament=data["tournament"],
            date=data["date"],
            match=data["match"],
            label=data.get("label", ""),
            kind=data.get("kind", "full"),
            playhead_frame=data.get("playhead_frame"),
            seconds_around=data.get("seconds_around"),
            opponent=data.get("opponent", ""),
            match_index=data.get("match_index"),
            immediate=bool(data.get("immediate", False)),
            payload=dict(data.get("payload") or {}),
            status=JobStatus(data.get("status", "pending")),
            percent=float(data.get("percent", 0.0)),
            phase=data.get("phase", "queued"),
            message=data.get("message", ""),
            output_filename=data.get("output_filename"),
            error=data.get("error"),
            created_at=float(data.get("created_at", time.time())),
            started_at=data.get("started_at"),
            finished_at=data.get("finished_at"),
        )
        return job


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, RenderJob] = {}
        self._listeners: dict[str, list[asyncio.Queue]] = {}
        self._lock = threading.Lock()
        # Global queue switch. Dispatcher only picks pending jobs while True.
        self._active: bool = False
        # Set whenever something changes that the dispatcher might care
        # about (new job, queue activated, cancel). Cleared by the
        # dispatcher when it goes back to waiting.
        self.wake = threading.Event()
        # Persistence hook installed by main.py at startup. Called with no
        # args after every state change; defaults to no-op so unit tests
        # don't need to wire persistence.
        self.on_state_changed: Callable[[], None] = lambda: None

    # ---- Job lifecycle ---------------------------------------------------

    def enqueue(
        self,
        team: str,
        tournament: str,
        date: str,
        match: str,
        *,
        label: str = "",
        kind: str = "full",
        playhead_frame: Optional[int] = None,
        seconds_around: Optional[float] = None,
        opponent: str = "",
        match_index: Optional[int] = None,
        immediate: bool = False,
        payload: Optional[dict[str, Any]] = None,
    ) -> RenderJob:
        """Create a pending job.

        If `immediate=False` (default), the dispatcher will pick it up
        when the queue is active. If `immediate=True` the job is marked
        to bypass the dispatcher - the caller is expected to spawn a
        worker thread that runs the job directly. Immediate jobs are
        excluded from `next_pending()` so the dispatcher never tries to
        race-run them.
        """
        job = RenderJob(
            id=uuid.uuid4().hex[:12],
            team=team,
            tournament=tournament,
            date=date,
            match=match,
            label=label,
            kind=kind,
            playhead_frame=playhead_frame,
            seconds_around=seconds_around,
            opponent=opponent,
            match_index=match_index,
            immediate=immediate,
            payload=dict(payload or {}),
        )
        with self._lock:
            self._jobs[job.id] = job
            self._listeners[job.id] = []
        self._broadcast(job)
        self._notify_changed()
        self.wake.set()
        return job

    def get(self, job_id: str) -> Optional[RenderJob]:
        return self._jobs.get(job_id)

    def list_jobs(self) -> list[RenderJob]:
        return list(self._jobs.values())

    def list_team_jobs(self, team: str) -> list[RenderJob]:
        """Jobs belonging to `team`, sorted newest first."""
        out = [j for j in self._jobs.values() if j.team == team]
        out.sort(key=lambda j: j.created_at, reverse=True)
        return out

    def next_pending(self) -> Optional[RenderJob]:
        """Oldest non-immediate pending job (FIFO), or None when drained.

        Immediate jobs run via their own thread (see `kick_off_immediate`
        in `render_job.py`); the dispatcher must not pick them up or it
        could race-run the same job twice.
        """
        candidates = [
            j
            for j in self._jobs.values()
            if j.status == JobStatus.PENDING and not j.immediate
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda j: j.created_at)
        return candidates[0]

    def update(self, job_id: str, **changes) -> Optional[RenderJob]:
        job = self._jobs.get(job_id)
        if job is None:
            return None
        for k, v in changes.items():
            if hasattr(job, k):
                setattr(job, k, v)
        self._broadcast(job)
        self._notify_changed()
        return job

    def mark_started(self, job_id: str) -> None:
        self.update(
            job_id,
            status=JobStatus.RUNNING,
            started_at=time.time(),
            phase="rendering",
        )

    def mark_done(self, job_id: str, output_filename: str) -> None:
        self.update(
            job_id,
            status=JobStatus.DONE,
            percent=100.0,
            phase="done",
            output_filename=output_filename,
            finished_at=time.time(),
        )

    def mark_failed(self, job_id: str, error: str) -> None:
        self.update(
            job_id,
            status=JobStatus.FAILED,
            phase="failed",
            error=error,
            finished_at=time.time(),
        )

    def mark_cancelled(self, job_id: str) -> None:
        self.update(
            job_id,
            status=JobStatus.CANCELLED,
            phase="cancelled",
            finished_at=time.time(),
        )

    def request_cancel(self, job_id: str) -> bool:
        """Ask a live job to cancel. Returns False if the job is already
        terminal or doesn't exist.

        Pending jobs are flipped to `cancelled` immediately - they were
        never started. Running jobs get the cancel_event set; the renderer
        polls this between frames and aborts cleanly.
        """
        job = self._jobs.get(job_id)
        if job is None or job.status not in _LIVE_STATUSES:
            return False
        job.cancel_event.set()
        if job.status == JobStatus.PENDING:
            # Never ran - move straight to terminal so it disappears from
            # the queue without going through the renderer.
            self.mark_cancelled(job_id)
        else:
            self.update(
                job_id, phase="cancelling", message="Cancellation requested"
            )
        # Wake the dispatcher so it re-checks; the cancelled job won't be
        # picked up but a different pending job might be next.
        self.wake.set()
        return True

    def delete(self, job_id: str) -> bool:
        """Remove a terminal job from the registry. 4xx if still live."""
        job = self._jobs.get(job_id)
        if job is None:
            return False
        if job.status not in _TERMINAL_STATUSES:
            return False
        with self._lock:
            self._jobs.pop(job_id, None)
            self._listeners.pop(job_id, None)
        self._notify_changed()
        return True

    # ---- Global queue switch --------------------------------------------

    def is_active(self) -> bool:
        return self._active

    def set_active(self, active: bool) -> None:
        if self._active == active:
            return
        self._active = active
        logger.info("render queue %s", "started" if active else "stopped")
        self._notify_changed()
        # Wake dispatcher so it picks up the new state promptly.
        self.wake.set()

    def queue_summary(self) -> dict[str, Any]:
        pending = sum(
            1 for j in self._jobs.values() if j.status == JobStatus.PENDING
        )
        running = sum(
            1 for j in self._jobs.values() if j.status == JobStatus.RUNNING
        )
        return {
            "active": self._active,
            "pending": pending,
            "running": running,
            "total": len(self._jobs),
        }

    # ---- Persistence wiring ---------------------------------------------

    def load_from(self, jobs: list[RenderJob], active: bool) -> None:
        """Replace state from persisted data. Called once at startup.

        Any job that was marked RUNNING when the process died gets
        flipped to FAILED - the renderer can't resume mid-frame, and
        leaving them as 'running' would confuse the UI forever.
        """
        with self._lock:
            self._jobs.clear()
            self._listeners.clear()
            for j in jobs:
                if j.status == JobStatus.RUNNING:
                    j.status = JobStatus.FAILED
                    j.phase = "failed"
                    j.error = "Server restarted while this job was running"
                    j.finished_at = j.finished_at or time.time()
                self._jobs[j.id] = j
                self._listeners[j.id] = []
        # Queue active state is intentionally NOT persisted - the user has
        # to explicitly start it after a restart so we never auto-render
        # on a crash loop. (Pass-through arg kept for symmetry / future.)
        self._active = active
        logger.info(
            "loaded %d job(s) from persistence (active=%s)",
            len(jobs),
            active,
        )

    def _notify_changed(self) -> None:
        try:
            self.on_state_changed()
        except Exception as exc:  # never let persistence break the manager
            logger.warning("persistence hook raised: %s", exc)

    # ---- SSE listeners --------------------------------------------------

    def subscribe(self, job_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        with self._lock:
            self._listeners.setdefault(job_id, []).append(q)
        job = self._jobs.get(job_id)
        if job is not None:
            try:
                q.put_nowait(job.to_dict())
            except Exception:
                pass
        return q

    def unsubscribe(self, job_id: str, q: asyncio.Queue) -> None:
        with self._lock:
            if job_id in self._listeners and q in self._listeners[job_id]:
                self._listeners[job_id].remove(q)

    def _broadcast(self, job: RenderJob) -> None:
        snapshot = job.to_dict()
        with self._lock:
            queues = list(self._listeners.get(job.id, []))
        for q in queues:
            try:
                q.put_nowait(snapshot)
            except Exception:
                pass


JOBS = JobManager()
