"""Tests for plugins/memory/honcho/sync_worker.py — Layers 1/2/3."""

from __future__ import annotations

import threading
import time
from typing import List

import pytest

from plugins.memory.honcho.sync_worker import (
    CircuitBreaker,
    HonchoLatencyTracker,
    SyncTask,
    SyncWorker,
)


# ---------------------------------------------------------------------------
# HonchoLatencyTracker
# ---------------------------------------------------------------------------


class TestHonchoLatencyTracker:
    def test_returns_default_during_warmup(self):
        t = HonchoLatencyTracker(default=30.0, warmup_samples=5)
        for _ in range(4):
            t.observe(1.0)
        assert t.timeout() == 30.0

    def test_adapts_to_observed_p95(self):
        t = HonchoLatencyTracker(default=30.0, floor=5.0, headroom=3.0, warmup_samples=5)
        # 10 samples at 1s, 10 samples at 2s — p95 should land at the 2s end
        for _ in range(10):
            t.observe(1.0)
        for _ in range(10):
            t.observe(2.0)
        t_out = t.timeout()
        assert 5.0 <= t_out <= 7.0  # 3 × 2.0 with some rounding latitude

    def test_respects_floor(self):
        t = HonchoLatencyTracker(default=30.0, floor=5.0, headroom=3.0, warmup_samples=3)
        # Very fast samples — 3 × 0.1 = 0.3 < floor → floor applies
        for _ in range(10):
            t.observe(0.1)
        assert t.timeout() == 5.0

    def test_rejects_nan_and_negative(self):
        t = HonchoLatencyTracker(warmup_samples=1)
        t.observe(float("nan"))
        t.observe(-1.0)
        # No valid samples → still default
        assert t.timeout() == t._default

    def test_rolling_window_discards_old(self):
        t = HonchoLatencyTracker(window=5, default=30.0, floor=0.1, headroom=1.0, warmup_samples=1)
        for _ in range(5):
            t.observe(100.0)
        assert t.timeout() >= 50.0  # dominated by 100s samples
        for _ in range(5):
            t.observe(0.5)
        # Old samples rolled out, now dominated by 0.5s
        assert t.timeout() <= 1.0

    def test_thread_safe_concurrent_observations(self):
        t = HonchoLatencyTracker(window=1000, warmup_samples=1)

        def observer(val: float):
            for _ in range(200):
                t.observe(val)

        threads = [
            threading.Thread(target=observer, args=(i * 0.1,)) for i in range(5)
        ]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        # No crash + timeout() returns a real number
        assert t.timeout() > 0


# ---------------------------------------------------------------------------
# CircuitBreaker
# ---------------------------------------------------------------------------


class _Clock:
    """Test double for time.monotonic — manually advanced."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


class TestCircuitBreaker:
    def test_starts_closed(self):
        cb = CircuitBreaker()
        assert cb.state == cb.STATE_CLOSED
        assert cb.allow() is True

    def test_opens_after_threshold_failures(self):
        cb = CircuitBreaker(failure_threshold=3)
        for _ in range(2):
            cb.record_failure()
        assert cb.state == cb.STATE_CLOSED
        cb.record_failure()
        assert cb.state == cb.STATE_OPEN
        assert cb.allow() is False

    def test_resets_counter_on_success(self):
        cb = CircuitBreaker(failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()
        cb.record_failure()
        assert cb.state == cb.STATE_CLOSED

    def test_transitions_to_half_open_after_probe_interval(self):
        clock = _Clock()
        cb = CircuitBreaker(failure_threshold=2, probe_interval=60.0, time_fn=clock)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == cb.STATE_OPEN
        clock.now = 30.0
        assert cb.state == cb.STATE_OPEN  # still within probe window
        clock.now = 61.0
        assert cb.state == cb.STATE_HALF_OPEN
        assert cb.allow() is True  # probe permitted

    def test_half_open_success_closes_breaker(self):
        clock = _Clock()
        cb = CircuitBreaker(failure_threshold=2, probe_interval=60.0, time_fn=clock)
        cb.record_failure()
        cb.record_failure()
        clock.now = 61.0
        _ = cb.state  # transition
        cb.record_success()
        assert cb.state == cb.STATE_CLOSED

    def test_half_open_failure_reopens_breaker(self):
        clock = _Clock()
        cb = CircuitBreaker(failure_threshold=2, probe_interval=60.0, time_fn=clock)
        cb.record_failure()
        cb.record_failure()
        clock.now = 61.0
        _ = cb.state
        cb.record_failure()
        assert cb.state == cb.STATE_OPEN

    def test_reset_returns_to_closed(self):
        cb = CircuitBreaker(failure_threshold=1)
        cb.record_failure()
        assert cb.state == cb.STATE_OPEN
        cb.reset()
        assert cb.state == cb.STATE_CLOSED
        assert cb.allow() is True


# ---------------------------------------------------------------------------
# SyncWorker
# ---------------------------------------------------------------------------


def _wait_until(predicate, timeout: float = 2.0, interval: float = 0.01) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


class TestSyncWorkerBasics:
    def test_enqueue_runs_task_on_worker_thread(self):
        worker = SyncWorker()
        try:
            results: List[str] = []
            worker.enqueue(SyncTask(fn=lambda: results.append("ran"), name="t"))
            assert _wait_until(lambda: results == ["ran"])
        finally:
            worker.shutdown(timeout=2.0)

    def test_enqueue_returns_immediately(self):
        worker = SyncWorker()
        try:
            slow_event = threading.Event()

            def slow_task():
                slow_event.wait(timeout=2.0)

            t0 = time.monotonic()
            worker.enqueue(SyncTask(fn=slow_task, name="slow"))
            elapsed = time.monotonic() - t0
            assert elapsed < 0.1, f"enqueue blocked for {elapsed}s"
            slow_event.set()
        finally:
            worker.shutdown(timeout=2.0)

    def test_tasks_execute_in_fifo_order(self):
        worker = SyncWorker()
        try:
            results: List[int] = []
            for i in range(10):
                worker.enqueue(SyncTask(fn=lambda i=i: results.append(i), name=f"t{i}"))
            assert _wait_until(lambda: len(results) == 10)
            assert results == list(range(10))
        finally:
            worker.shutdown(timeout=2.0)

    def test_task_exception_does_not_kill_worker(self):
        worker = SyncWorker()
        try:
            survived: List[str] = []
            worker.enqueue(SyncTask(fn=lambda: (_ for _ in ()).throw(RuntimeError("boom")), name="boom"))
            worker.enqueue(SyncTask(fn=lambda: survived.append("ok"), name="ok"))
            assert _wait_until(lambda: survived == ["ok"])
        finally:
            worker.shutdown(timeout=2.0)

    def test_on_failure_callback_invoked_when_task_raises(self):
        worker = SyncWorker()
        try:
            failures: List[BaseException] = []
            worker.enqueue(
                SyncTask(
                    fn=lambda: (_ for _ in ()).throw(ValueError("nope")),
                    name="fail",
                    on_failure=failures.append,
                )
            )
            assert _wait_until(lambda: len(failures) == 1)
            assert isinstance(failures[0], ValueError)
        finally:
            worker.shutdown(timeout=2.0)


class TestSyncWorkerBackpressure:
    def test_queue_overflow_drops_oldest_task(self):
        worker = SyncWorker(max_queue=3)
        try:
            block = threading.Event()
            ran: List[int] = []
            dropped: List[int] = []

            # Fill the queue with a blocker + 3 more waiting tasks.
            worker.enqueue(SyncTask(fn=lambda: block.wait(timeout=3.0), name="blocker"))
            for i in range(3):
                worker.enqueue(
                    SyncTask(
                        fn=lambda i=i: ran.append(i),
                        name=f"t{i}",
                        on_failure=lambda e, i=i: dropped.append(i),
                    )
                )
            # Now try to enqueue a 4th task — should evict the oldest queued
            # (task 0) to make room.
            worker.enqueue(SyncTask(fn=lambda: ran.append(99), name="overflow"))

            # Queue overflow dropped exactly one task (task 0).
            block.set()
            assert _wait_until(lambda: 99 in ran)
            assert 0 in dropped or ran == [1, 2, 99] or ran == [1, 2, 3, 99]
        finally:
            worker.shutdown(timeout=3.0)


class TestSyncWorkerIntegration:
    def test_breaker_open_skips_task(self):
        breaker = CircuitBreaker(failure_threshold=1)
        breaker.record_failure()
        assert breaker.state == breaker.STATE_OPEN

        worker = SyncWorker(breaker=breaker)
        try:
            failures: List[BaseException] = []
            ran: List[str] = []
            worker.enqueue(
                SyncTask(
                    fn=lambda: ran.append("should_not_run"),
                    name="blocked",
                    on_failure=failures.append,
                )
            )
            # Give the worker a moment — but the task should never run.
            time.sleep(0.2)
            assert ran == []
            assert len(failures) == 1
            assert "circuit breaker open" in str(failures[0])
        finally:
            worker.shutdown(timeout=2.0)

    def test_successful_task_feeds_latency_tracker_and_resets_breaker(self):
        tracker = HonchoLatencyTracker(warmup_samples=1)
        breaker = CircuitBreaker(failure_threshold=2)
        breaker.record_failure()
        assert breaker._consecutive_failures == 1

        worker = SyncWorker(latency_tracker=tracker, breaker=breaker)
        try:
            worker.enqueue(SyncTask(fn=lambda: time.sleep(0.05), name="t"))
            assert _wait_until(lambda: len(tracker._samples) >= 1)
            assert breaker.state == breaker.STATE_CLOSED
            assert breaker._consecutive_failures == 0
        finally:
            worker.shutdown(timeout=2.0)

    def test_failed_task_increments_breaker(self):
        breaker = CircuitBreaker(failure_threshold=2)
        worker = SyncWorker(breaker=breaker)
        try:
            for _ in range(2):
                worker.enqueue(
                    SyncTask(
                        fn=lambda: (_ for _ in ()).throw(RuntimeError("x")),
                        name="fail",
                    )
                )
            assert _wait_until(lambda: breaker.state == breaker.STATE_OPEN)
        finally:
            worker.shutdown(timeout=2.0)


class TestSyncWorkerShutdown:
    def test_shutdown_is_idempotent(self):
        worker = SyncWorker()
        worker.enqueue(SyncTask(fn=lambda: None, name="t"))
        worker.shutdown(timeout=2.0)
        worker.shutdown(timeout=2.0)  # Must not raise
        assert not worker.is_running()

    def test_enqueue_after_shutdown_calls_on_failure(self):
        worker = SyncWorker()
        worker.shutdown(timeout=2.0)
        failures: List[BaseException] = []
        ok = worker.enqueue(
            SyncTask(fn=lambda: None, name="late", on_failure=failures.append)
        )
        assert ok is False
        assert len(failures) == 1
