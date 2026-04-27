"""Background sync worker for the Honcho memory provider.

The post-response sync path was previously a per-turn thread spawn with a
best-effort ``join(timeout=5.0)`` on the prior turn's thread.  That forced
``run_conversation`` to wait up to 5 seconds at the start of every turn if
the previous turn's sync was still in flight, and it serialized all sync
work on a single transient thread that the caller had to coordinate with.

This module replaces that pattern with:

  - :class:`SyncWorker` — persistent daemon thread draining a bounded
    queue of sync/write tasks.  Tasks are submitted with ``enqueue()`` and
    return immediately; the caller is never blocked by Honcho latency.

  - :class:`HonchoLatencyTracker` — rolling p95 observer that gives the
    client an adaptive timeout with sensible cold-start defaults (Layer 2
    of the timeout-ceiling rework).

  - :class:`CircuitBreaker` — consecutive-failure tripwire that flips to
    a degraded state after repeated timeouts and probes for recovery in
    the background (Layer 3).  While open, sync tasks are persisted to a
    local backlog so the outage's worth of writes can be drained once
    Honcho is reachable again.

The three primitives compose: ``SyncWorker`` consults the breaker before
each task, records the outcome in the latency tracker, and feeds timeout
+ failure observations back to the breaker.  Nothing here depends on
``HonchoMemoryProvider`` — the worker takes plain callables so tests can
exercise each primitive in isolation.
"""

from __future__ import annotations

import collections
import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable, Deque, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Latency tracker — Layer 2
# ---------------------------------------------------------------------------


class HonchoLatencyTracker:
    """Rolling p95 observer for Honcho call latencies.

    Provides an adaptive HTTP timeout that scales with observed backend
    latency.  Hosted Honcho settles to ~1-3s; self-hosted instances with
    slow cold starts naturally scale up.  Thread-safe: the worker thread
    records observations, any thread may read the current timeout.
    """

    def __init__(
        self,
        *,
        window: int = 20,
        default: float = 30.0,
        floor: float = 5.0,
        headroom: float = 3.0,
        warmup_samples: int = 5,
    ) -> None:
        self._samples: Deque[float] = collections.deque(maxlen=window)
        self._default = float(default)
        self._floor = float(floor)
        self._headroom = float(headroom)
        self._warmup = int(warmup_samples)
        self._lock = threading.Lock()

    def observe(self, seconds: float) -> None:
        """Record a successful call's wall-clock latency (seconds)."""
        if seconds < 0 or seconds != seconds:  # NaN check
            return
        with self._lock:
            self._samples.append(float(seconds))

    def timeout(self) -> float:
        """Return the adaptive timeout for the next call.

        During warmup (< warmup_samples observations) returns the default.
        Once warm, returns ``max(floor, headroom × p95(samples))``.
        """
        with self._lock:
            n = len(self._samples)
            if n < self._warmup:
                return self._default
            sorted_samples = sorted(self._samples)
            # Nearest-rank p95: index = ceil(0.95 * n) - 1, clamped.
            idx = min(n - 1, max(0, int(round(0.95 * (n - 1)))))
            p95 = sorted_samples[idx]
        return max(self._floor, self._headroom * p95)

    def reset(self) -> None:
        with self._lock:
            self._samples.clear()


# ---------------------------------------------------------------------------
# Circuit breaker — Layer 3
# ---------------------------------------------------------------------------


class CircuitBreaker:
    """Consecutive-failure tripwire with periodic probe recovery.

    States:
      - ``closed``   — traffic flows normally (the happy path)
      - ``open``     — recent consecutive failures > threshold; skip calls
      - ``half_open`` — probe window; one test call is allowed through

    Transitions:
      - closed → open after ``failure_threshold`` consecutive failures
      - open → half_open after ``probe_interval`` seconds
      - half_open → closed on a successful probe
      - half_open → open on a failed probe

    Thread-safe.  The worker consults ``allow()`` before each task and
    reports the outcome via ``record_success()`` / ``record_failure()``.
    """

    STATE_CLOSED = "closed"
    STATE_OPEN = "open"
    STATE_HALF_OPEN = "half_open"

    def __init__(
        self,
        *,
        failure_threshold: int = 3,
        probe_interval: float = 60.0,
        time_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._failure_threshold = int(failure_threshold)
        self._probe_interval = float(probe_interval)
        self._time_fn = time_fn
        self._lock = threading.Lock()
        self._state = self.STATE_CLOSED
        self._consecutive_failures = 0
        self._opened_at: Optional[float] = None

    @property
    def state(self) -> str:
        with self._lock:
            self._maybe_transition_to_probe()
            return self._state

    def allow(self) -> bool:
        """Return True iff a call should proceed now."""
        with self._lock:
            self._maybe_transition_to_probe()
            return self._state != self.STATE_OPEN

    def record_success(self) -> None:
        with self._lock:
            self._consecutive_failures = 0
            if self._state != self.STATE_CLOSED:
                logger.info("Honcho circuit breaker: recovered → closed")
            self._state = self.STATE_CLOSED
            self._opened_at = None

    def record_failure(self) -> None:
        with self._lock:
            self._consecutive_failures += 1
            if self._state == self.STATE_HALF_OPEN:
                self._state = self.STATE_OPEN
                self._opened_at = self._time_fn()
                logger.warning("Honcho circuit breaker: probe failed → open")
                return
            if (
                self._state == self.STATE_CLOSED
                and self._consecutive_failures >= self._failure_threshold
            ):
                self._state = self.STATE_OPEN
                self._opened_at = self._time_fn()
                logger.warning(
                    "Honcho circuit breaker: %d consecutive failures → open",
                    self._consecutive_failures,
                )

    def reset(self) -> None:
        with self._lock:
            self._state = self.STATE_CLOSED
            self._consecutive_failures = 0
            self._opened_at = None

    def _maybe_transition_to_probe(self) -> None:
        # Caller must hold the lock.
        if self._state == self.STATE_OPEN and self._opened_at is not None:
            if self._time_fn() - self._opened_at >= self._probe_interval:
                self._state = self.STATE_HALF_OPEN
                logger.info(
                    "Honcho circuit breaker: probe window → half_open"
                )


# ---------------------------------------------------------------------------
# Sync worker — Layer 1
# ---------------------------------------------------------------------------


@dataclass
class SyncTask:
    """A unit of work for the sync worker.

    ``fn`` runs on the worker thread.  ``name`` is a human-readable label
    used in logs and for backlog replay.  ``on_failure`` is optional: if
    set, it's called with the exception on breaker-open deferral or when
    all retries are exhausted so callers can persist the task to a
    durable backlog.
    """

    fn: Callable[[], None]
    name: str = "sync"
    on_failure: Optional[Callable[[BaseException], None]] = None


class SyncWorker:
    """Persistent daemon thread draining a bounded task queue.

    This replaces the per-turn ``threading.Thread(target=_sync).start()``
    pattern so ``sync_turn`` returns immediately instead of coordinating
    thread handoff on every turn.  Runs ``SyncTask`` callables serially
    on a dedicated thread — serialization is intentional because Honcho
    session writes must be ordered per-session to avoid re-ordering
    messages, and the worker handles one session per provider.

    Queue overflow (producer faster than Honcho can drain) drops the
    OLDEST task rather than blocking the producer.  This favors user-
    facing responsiveness over write fidelity in the pathological case,
    and the dropped task still has its ``on_failure`` callback invoked
    so it can be appended to a durable backlog.

    The worker is lazy: the thread starts on first ``enqueue()`` call
    and runs until ``shutdown()``.  ``shutdown()`` is idempotent.
    """

    def __init__(
        self,
        *,
        max_queue: int = 64,
        latency_tracker: Optional[HonchoLatencyTracker] = None,
        breaker: Optional[CircuitBreaker] = None,
        thread_name: str = "honcho-sync-worker",
    ) -> None:
        self._queue: queue.Queue[Optional[SyncTask]] = queue.Queue(maxsize=max_queue)
        self._thread: Optional[threading.Thread] = None
        self._thread_name = thread_name
        self._shutdown = False
        self._lock = threading.Lock()
        self._latency_tracker = latency_tracker
        self._breaker = breaker
        self._dropped = 0

    # -- lifecycle -----------------------------------------------------------

    def _ensure_started(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            if self._shutdown:
                return
            self._thread = threading.Thread(
                target=self._run,
                name=self._thread_name,
                daemon=True,
            )
            self._thread.start()

    def shutdown(self, *, timeout: float = 5.0) -> None:
        """Signal the worker to drain and exit; wait up to ``timeout`` seconds."""
        with self._lock:
            if self._shutdown:
                return
            self._shutdown = True
            thread = self._thread
        try:
            # Sentinel triggers clean exit from the loop.
            self._queue.put_nowait(None)
        except queue.Full:
            pass
        if thread is not None:
            thread.join(timeout=timeout)

    # -- producer interface --------------------------------------------------

    def enqueue(self, task: SyncTask) -> bool:
        """Submit a task.  Returns False if the task was dropped.

        Breaker-open tasks are dropped synchronously and ``on_failure`` is
        called so the caller can persist them.  Queue-full tasks evict
        the oldest task (which also gets its ``on_failure`` called) to
        keep the pipeline moving under load.
        """
        if self._shutdown:
            if task.on_failure is not None:
                try:
                    task.on_failure(RuntimeError("sync worker is shutting down"))
                except Exception:
                    pass
            return False

        breaker = self._breaker
        if breaker is not None and not breaker.allow():
            if task.on_failure is not None:
                try:
                    task.on_failure(RuntimeError("circuit breaker open"))
                except Exception:
                    pass
            return False

        self._ensure_started()

        try:
            self._queue.put_nowait(task)
            return True
        except queue.Full:
            # Evict the oldest queued task to make room; its failure
            # callback still fires so the backlog can capture it.
            try:
                victim = self._queue.get_nowait()
                self._dropped += 1
                if victim is not None and victim.on_failure is not None:
                    try:
                        victim.on_failure(
                            RuntimeError("sync queue overflow — task dropped")
                        )
                    except Exception:
                        pass
            except queue.Empty:
                pass
            try:
                self._queue.put_nowait(task)
                return True
            except queue.Full:
                if task.on_failure is not None:
                    try:
                        task.on_failure(RuntimeError("sync queue full"))
                    except Exception:
                        pass
                return False

    # -- worker loop ---------------------------------------------------------

    def _run(self) -> None:
        while True:
            try:
                task = self._queue.get(timeout=1.0)
            except queue.Empty:
                if self._shutdown:
                    return
                continue

            if task is None:  # sentinel
                return

            started = time.monotonic()
            ok = False
            error: Optional[BaseException] = None
            try:
                task.fn()
                ok = True
            except BaseException as e:  # task bodies may raise anything
                error = e
                logger.debug("Honcho sync task %s failed: %s", task.name, e)

            elapsed = time.monotonic() - started
            if ok:
                if self._latency_tracker is not None:
                    self._latency_tracker.observe(elapsed)
                if self._breaker is not None:
                    self._breaker.record_success()
            else:
                if self._breaker is not None:
                    self._breaker.record_failure()
                if task.on_failure is not None and error is not None:
                    try:
                        task.on_failure(error)
                    except Exception:
                        pass

    # -- introspection (for hermes honcho status etc.) -----------------------

    def qsize(self) -> int:
        return self._queue.qsize()

    def dropped(self) -> int:
        return self._dropped

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()
