"""Background dispatcher that runs queued render jobs one at a time.

Lifecycle:
  - `start()` spawns a daemon thread that loops forever until `stop()`
    is called (typically at app shutdown).
  - The loop waits on `JOBS.wake` (an event), with a periodic timeout so
    it self-heals if a wake signal is ever missed.
  - When woken, it checks if the global queue is `active` and there's a
    pending job. If both, it runs the job synchronously in this thread
    before looking for the next one.

Concurrency: exactly one worker thread. Renders are GPU/disk-heavy, so
parallelism rarely helps the *total* throughput and often makes the
machine unusable. The single-worker invariant is what lets the user keep
editing comfortably while a render is running.
"""

from __future__ import annotations

import logging
import threading

from ..render.renderer import RenderCancelled
from .manager import JOBS
from .render_job import run_render

logger = logging.getLogger(__name__)


# Maximum time between forced wakeups, in seconds. The dispatcher is
# event-driven via JOBS.wake, but a periodic re-check guards against
# missed wakes and lets us observe the stop flag.
_WAKE_TIMEOUT_S = 5.0


class Dispatcher:
    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name="render-dispatcher", daemon=True
        )
        self._thread.start()
        logger.info("render dispatcher started")

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
            if JOBS.is_active():
                job = JOBS.next_pending()
                if job is not None:
                    self._run_one(job)
                    continue

            # Idle path: nothing to do (queue paused, or no pending jobs).
            # Clear the wake flag BEFORE re-checking, then re-check after
            # the clear. Without that re-check we'd lose any wake signal
            # delivered between clear and wait (set/clear race).
            JOBS.wake.clear()
            if JOBS.is_active() and JOBS.next_pending() is not None:
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
            "dispatcher running job %s (%s / %s)",
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
        JOBS.wake.wait(timeout=_WAKE_TIMEOUT_S)


DISPATCHER = Dispatcher()
