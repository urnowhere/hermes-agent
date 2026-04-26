"""Auxiliary task health tracking and escalation.

When auxiliary LLM tasks (``title_generation``, ``session_search``,
``compression``, ``vision``, ...) fail repeatedly, we need to surface that to
the user instead of silently logging a warning.  Real-world reports show a
single revoked OpenRouter key can leave 45+ sessions with NULL titles over
weeks — invisible because the main conversation keeps working.

This module provides a small, in-process tracker for consecutive-failure
counts per task.  Once the threshold is hit, a registered notifier callback
fires (typically wired to ``HermesAgent._emit_auxiliary_failure`` so the user
sees the warning in the CLI / Telegram / whatever active platform).

Scope intentionally narrow: this is *not* an event bus.  It tracks a counter,
fires one warning at the threshold, and avoids spam re-emission until either
a success resets the counter or another ``HERMES_AUX_REEMIT_INTERVAL`` (default
10) failures accumulate.

The tracker is also read by ``hermes doctor`` to surface current failure
state.

See issue #15775.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass, replace
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    try:
        raw = os.environ.get(name)
        if raw is None or not raw.strip():
            return default
        value = int(raw.strip())
        return value if value > 0 else default
    except (TypeError, ValueError):
        return default


@dataclass
class TaskHealth:
    """Per-task health snapshot."""

    task: str
    consecutive_failures: int = 0
    total_failures: int = 0
    total_successes: int = 0
    last_error: Optional[str] = None
    last_error_class: Optional[str] = None
    last_failure_ts: Optional[float] = None
    last_success_ts: Optional[float] = None
    last_warning_at_count: int = 0  # count at which we last emitted a warning


class AuxiliaryHealthTracker:
    """Thread-safe tracker for auxiliary-task call outcomes.

    Records consecutive failures per task.  When the count reaches
    ``threshold`` (default 3), invokes the registered notifier exactly once.
    Re-emits only after a success resets the counter, or after another
    ``reemit_interval`` failures (default 10) past the last warning.
    """

    DEFAULT_THRESHOLD = 3
    DEFAULT_REEMIT_INTERVAL = 10

    def __init__(
        self,
        threshold: Optional[int] = None,
        reemit_interval: Optional[int] = None,
    ) -> None:
        self.threshold = threshold or _env_int(
            "HERMES_AUX_FAILURE_THRESHOLD", self.DEFAULT_THRESHOLD
        )
        self.reemit_interval = reemit_interval or _env_int(
            "HERMES_AUX_REEMIT_INTERVAL", self.DEFAULT_REEMIT_INTERVAL
        )
        self._lock = threading.Lock()
        self._states: Dict[str, TaskHealth] = {}
        self._notifier: Optional[Callable[[str, TaskHealth], None]] = None
        # Owner identity for the currently-registered notifier.  Stored as a
        # plain reference (not a weakref) because the gateway-per-message
        # AIAgent's lifetime is bounded by ``run_conversation`` + ``close``;
        # the close path calls ``clear_notifier_if_owner`` so we never hold
        # the agent past its useful lifetime.  ``None`` means "no owner",
        # which matches "no notifier".
        self._notifier_owner: Optional[Any] = None

    # ── Wiring ───────────────────────────────────────────────────────

    def set_notifier(
        self,
        notifier: Optional[Callable[[str, TaskHealth], None]],
        owner: Optional[Any] = None,
    ) -> None:
        """Register the escalation callback.

        Notifier signature: ``(task: str, health: TaskHealth) -> None``.
        Invoked from outside the lock — it is safe to take time in the
        callback.  Exceptions raised by the notifier are caught and logged
        so a broken UI plumbing path can never break auxiliary calls.

        ``owner`` identifies the registrant (typically the AIAgent
        instance).  Pair with :meth:`clear_notifier_if_owner` so a
        gateway-per-message agent can clean up its registration on
        shutdown without clobbering a successor that has already taken
        over.  Passing ``notifier=None`` unconditionally clears (and
        clears the owner too).
        """
        with self._lock:
            self._notifier = notifier
            self._notifier_owner = owner if notifier is not None else None

    def clear_notifier_if_owner(self, owner: Any) -> bool:
        """Clear the registered notifier ONLY if ``owner`` currently owns it.

        Returns True when the registration was cleared, False when the
        current owner is someone else (or there is no notifier).  This
        is the safe primitive an AIAgent's shutdown path calls so it
        cannot accidentally clear a successor agent's notifier.

        The gateway constructs a fresh AIAgent per message; if message N's
        agent installed (and clobbered the previous registration), then
        the previous agent's ``cleanup`` should NOT then drop message N's
        notifier.  Owner-aware clearing guarantees this.
        """
        if owner is None:
            return False
        with self._lock:
            if self._notifier_owner is owner:
                self._notifier = None
                self._notifier_owner = None
                return True
            return False

    def has_notifier(self) -> bool:
        """Return True when a notifier is currently registered.

        Retained for ``hermes doctor`` introspection and tests.  Note: not
        used as a first-agent-wins guard in ``AIAgent.__init__`` anymore
        — the gateway constructs a fresh AIAgent per message, so a
        permanent first-wins guard would route warnings into a stale
        closure.  See ``AIAgent._install_auxiliary_health_notifier`` and
        ``AIAgent.__init__``'s ``install_auxiliary_notifier`` parameter
        for the current design.
        """
        with self._lock:
            return self._notifier is not None

    def get_notifier_owner(self) -> Optional[Any]:
        """Return the currently-registered notifier's owner (or ``None``).

        Used by tests that want to assert which agent currently owns the
        registration.  Not for production hot paths.
        """
        with self._lock:
            return self._notifier_owner

    # ── Recording ────────────────────────────────────────────────────

    def record_success(self, task: str) -> None:
        """Mark a successful call for ``task`` and reset the failure counter."""
        if not task:
            return
        with self._lock:
            state = self._states.setdefault(task, TaskHealth(task=task))
            state.consecutive_failures = 0
            state.last_warning_at_count = 0
            state.total_successes += 1
            state.last_success_ts = time.time()

    def record_failure(self, task: str, exc: BaseException) -> None:
        """Mark a failed call for ``task`` and maybe escalate.

        If the consecutive-failure count crosses the threshold (or the
        re-emit interval since the last warning), the registered notifier
        is invoked.  Notifier exceptions are caught and logged so a broken
        UI plumbing path can never break auxiliary calls.
        """
        if not task:
            return
        notifier_to_fire: Optional[Callable[[str, TaskHealth], None]] = None
        snapshot: Optional[TaskHealth] = None
        with self._lock:
            state = self._states.setdefault(task, TaskHealth(task=task))
            state.consecutive_failures += 1
            state.total_failures += 1
            state.last_error = str(exc)[:500]
            state.last_error_class = exc.__class__.__name__
            state.last_failure_ts = time.time()

            if self._should_escalate(state):
                state.last_warning_at_count = state.consecutive_failures
                notifier_to_fire = self._notifier
                snapshot = replace(state)  # shallow copy for thread-safe handoff

        if notifier_to_fire and snapshot is not None:
            try:
                notifier_to_fire(snapshot.task, snapshot)
            except Exception:
                logger.exception(
                    "AuxiliaryHealthTracker notifier raised for task=%s", task
                )

    def _should_escalate(self, state: TaskHealth) -> bool:
        """Return True when we should fire a user-visible warning."""
        # Below threshold: stay quiet.
        if state.consecutive_failures < self.threshold:
            return False
        # First crossing of the threshold.
        if state.last_warning_at_count == 0:
            return True
        # Already warned — only re-emit once another ``reemit_interval``
        # failures pile up since the last warning.
        return (
            state.consecutive_failures - state.last_warning_at_count
            >= self.reemit_interval
        )

    # ── Inspection (for doctor / status) ─────────────────────────────

    def should_escalate(self, task: str) -> bool:
        """Public predicate — returns True if ``task`` is currently failing
        past threshold and a warning would fire on the next failure."""
        with self._lock:
            state = self._states.get(task)
            if state is None:
                return False
            return state.consecutive_failures >= self.threshold

    def get_status(self) -> Dict[str, TaskHealth]:
        """Return a snapshot mapping of task → TaskHealth (copies, safe to
        iterate after the call returns).  Used by ``hermes doctor``."""
        with self._lock:
            return {task: replace(state) for task, state in self._states.items()}

    def get_failing_tasks(self) -> Dict[str, TaskHealth]:
        """Return only tasks currently above the failure threshold."""
        return {
            task: health
            for task, health in self.get_status().items()
            if health.consecutive_failures >= self.threshold
        }

    def clear(self, task: Optional[str] = None) -> None:
        """Reset state for ``task``, or all tasks if ``task is None``."""
        with self._lock:
            if task is None:
                self._states.clear()
            else:
                self._states.pop(task, None)


# ── Module-level singleton ─────────────────────────────────────────────

_tracker_lock = threading.Lock()
_tracker: Optional[AuxiliaryHealthTracker] = None


def get_tracker() -> AuxiliaryHealthTracker:
    """Return the process-wide AuxiliaryHealthTracker singleton."""
    global _tracker
    if _tracker is not None:
        return _tracker
    with _tracker_lock:
        if _tracker is None:
            _tracker = AuxiliaryHealthTracker()
        return _tracker


def reset_tracker_for_tests() -> None:
    """Test hook — discard the singleton so the next ``get_tracker()`` call
    returns a fresh instance.  Not for production use."""
    global _tracker
    with _tracker_lock:
        _tracker = None
