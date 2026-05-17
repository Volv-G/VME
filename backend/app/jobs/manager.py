"""In-memory render job registry with SSE-friendly event streams."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

logger = logging.getLogger(__name__)


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


# Non-terminal statuses (the job is still doing work).
_LIVE_STATUSES = {JobStatus.PENDING, JobStatus.RUNNING}


@dataclass
class RenderJob:
    id: str
    team: str
    tournament: str
    date: str
    match: str
    label: str = ""
    status: JobStatus = JobStatus.PENDING
    percent: float = 0.0
    phase: str = "queued"
    message: str = ""
    output_filename: Optional[str] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    # Worker thread polls this; set() asks the renderer to abort at the next
    # frame boundary. Not serialized.
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


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, RenderJob] = {}
        self._listeners: dict[str, list[asyncio.Queue]] = {}
        self._lock = threading.Lock()

    def create(
        self,
        team: str,
        tournament: str,
        date: str,
        match: str,
        label: str = "",
    ) -> RenderJob:
        job = RenderJob(
            id=uuid.uuid4().hex[:12],
            team=team,
            tournament=tournament,
            date=date,
            match=match,
            label=label,
        )
        with self._lock:
            self._jobs[job.id] = job
            self._listeners[job.id] = []
        self._broadcast(job)
        return job

    def get(self, job_id: str) -> Optional[RenderJob]:
        return self._jobs.get(job_id)

    def list_jobs(self) -> list[RenderJob]:
        return list(self._jobs.values())

    def update(self, job_id: str, **fields) -> Optional[RenderJob]:
        job = self._jobs.get(job_id)
        if job is None:
            return None
        for k, v in fields.items():
            if hasattr(job, k):
                setattr(job, k, v)
        self._broadcast(job)
        return job

    def mark_started(self, job_id: str) -> None:
        self.update(job_id, status=JobStatus.RUNNING, started_at=time.time(), phase="rendering")

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
        terminal (done/failed/cancelled) or doesn't exist."""
        job = self._jobs.get(job_id)
        if job is None or job.status not in _LIVE_STATUSES:
            return False
        job.cancel_event.set()
        # Broadcast so the UI sees `cancel_requested: true` immediately.
        self.update(job_id, phase="cancelling", message="Cancellation requested")
        return True

    # ---- SSE listeners ---------------------------------------------------

    def subscribe(self, job_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        with self._lock:
            self._listeners.setdefault(job_id, []).append(q)
        # Send the current state immediately.
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
