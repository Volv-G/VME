"""Background dispatchers that run queued jobs, one at a time per lane.

Lifecycle:
  - `start()` spawns one daemon thread PER LANE, looping until `stop()`
    is called (typically at app shutdown).
  - Each loop waits on that lane's `JOBS.wake` event, with a periodic
    timeout so it self-heals if a wake signal is ever missed.
  - When woken, a worker checks whether ITS lane is `active` and has a
    pending job in it. If both, it runs the job synchronously in its own
    thread before looking for the next one.

Concurrency: exactly one worker per lane, and today three lanes
(`render`, `upload` for YouTube, and `onedrive`). Renders stay serialized because they are
GPU/disk-heavy - parallelism rarely improves total throughput and often
makes the machine unusable to edit on. Uploads get their own thread
because they are network-bound and, far more importantly, because they
park: a YouTube upload waiting out a quota reset or the channel
video-count cap can sit for hours, and in a shared queue that is hours
of renders not happening. The lanes are independent in both directions -
stopping uploads because YouTube is refusing you no longer stops
rendering. OneDrive has its own worker for the same reason one step
down: a match going up to YouTube for an hour was an hour of reels not
going to OneDrive.
"""

from __future__ import annotations

import logging
import threading

from ..render.renderer import RenderCancelled
from .manager import JOBS, LANES
from .render_job import run_render

logger = logging.getLogger(__name__)


# Maximum time between forced wakeups, in seconds. The dispatcher is
# event-driven via JOBS.wake, but a periodic re-check guards against
# missed wakes and lets us observe the stop flag.
_WAKE_TIMEOUT_S = 5.0


class Dispatcher:
    """Worker for a single lane."""

    def __init__(self, lane: str) -> None:
        self.lane = lane
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name=f"{self.lane}-dispatcher", daemon=True
        )
        self._thread.start()
        logger.info("%s dispatcher started", self.lane)

    def stop(self, join_timeout: float = 2.0) -> None:
        self._stop.set()
        JOBS.wake.set()  # break the wait
        t = self._thread
        if t is not None:
            t.join(timeout=join_timeout)
        self._thread = None

    # ---- internal -------------------------------------------------------

    def _loop(self) -> None:
        while not self._stop.is_set():
            # Fast path: if the queue is active and there's work to do,
            # do it immediately - no waiting between jobs. This is what
            # lets a backlog of N jobs drain back-to-back.
            if JOBS.is_active(self.lane):
                job = JOBS.next_pending(self.lane)
                if job is not None:
                    self._run_one(job)
                    continue

            # Idle path: nothing to do (queue paused, or no pending jobs).
            # Clear the wake flag BEFORE re-checking, then re-check after
            # the clear. Without that re-check we'd lose any wake signal
            # delivered between clear and wait (set/clear race).
            JOBS.wake.event(self.lane).clear()
            if (
                JOBS.is_active(self.lane)
                and JOBS.next_pending(self.lane) is not None
            ):
                continue
            self._sleep_until_event()

    def _run_one(self, job) -> None:
        """Run a single job, translating exceptions into job-state changes."""
        # `request_cancel` flips pending jobs straight to CANCELLED, so we
        # normally won't see a pending+cancelled job here - but check
        # defensively in case future code sets the event without changing
        # status.
        if job.cancel_event.is_set():
            JOBS.mark_cancelled(job.id)
            return

        logger.info(
            "%s dispatcher running job %s (%s / %s)",
            self.lane,
            job.id,
            job.team,
            job.match,
        )
        try:
            run_render(job)
        except RenderCancelled:
            JOBS.mark_cancelled(job.id)
        except Exception as exc:
            logger.exception("render job %s failed: %s", job.id, exc)
            JOBS.mark_failed(job.id, str(exc))

    def _sleep_until_event(self) -> None:
        JOBS.wake.event(self.lane).wait(timeout=_WAKE_TIMEOUT_S)


class DispatcherGroup:
    """All lane workers, started and stopped together.

    The lanes are independent while running; only their process
    lifecycle is shared, so `main.py` keeps a single start/stop pair.
    """

    def __init__(self) -> None:
        self.dispatchers = {lane: Dispatcher(lane) for lane in LANES}

    def start(self) -> None:
        for d in self.dispatchers.values():
            d.start()

    def stop(self, join_timeout: float = 2.0) -> None:
        for d in self.dispatchers.values():
            d.stop(join_timeout=join_timeout)


DISPATCHER = DispatcherGroup()
