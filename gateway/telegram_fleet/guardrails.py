"""Fleet-level guardrails — size cap, rate limit, spawn approval.

Why these exist:

* **Fleet size cap** (`max_size`, default 16) — bounds how many Telegram bot
  tokens this manager can hold.  Prevents a runaway agent from spawning
  hundreds of bots that the user later has to clean up by hand.
* **Per-child rate limit** (`rate_limit_per_min`, default 30) — caps how many
  outbound messages a single child can send per minute.  Telegram's own
  global limit is 30/sec, but fan-out from a swarm can blow through that
  in seconds; this is a soft local limit applied per child before we even
  hit Telegram.
* **Spawn enabled gate** (`spawn_enabled`, default True) —
  controls whether the fleet is allowed to spawn new bots at all.
  ``telegram_spawn_bot`` always returns a deep link the human must tap
  (Telegram's platform contract); ``spawn_enabled=False`` prevents even
  generating that link and raises :class:`SpawnApprovalRequired`.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, Optional

from gateway.telegram_fleet.roster import FleetRoster


class FleetGuardrailError(Exception):
    """Raised when a fleet operation violates a configured guardrail."""


class SpawnApprovalRequired(FleetGuardrailError):
    """Raised when spawn is attempted but the operator hasn't enabled it."""


# ── Fleet size + spawn approval ────────────────────────────────────────


def check_can_spawn(roster: FleetRoster) -> None:
    """Verify the roster can accept a new spawn.

    Raises :class:`FleetGuardrailError` (or a subclass) when not.
    """
    if not roster.spawn_enabled:
        raise SpawnApprovalRequired(
            "spawn_enabled is False in telegram_fleet.yaml.  Set it to true "
            "to allow the fleet to mint new child-bot deep links."
        )

    active = len(roster.active_children())
    pending = len(roster.pending_children())
    if active + pending >= roster.max_size:
        raise FleetGuardrailError(
            f"fleet at capacity ({active} active + {pending} pending = {roster.max_size} max).  "
            f"Decommission an existing bot or raise max_size in telegram_fleet.yaml."
        )


# ── Per-child rate limit ───────────────────────────────────────────────


@dataclass
class _ChildRateState:
    capacity: int
    window_seconds: int
    timestamps: Deque[float]


class _RateLimiter:
    """Sliding-window rate limiter keyed by child username."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: Dict[str, _ChildRateState] = {}

    def configure(self, username: str, *, per_minute: int) -> None:
        username = (username or "").lstrip("@").lower()
        if not username:
            return
        with self._lock:
            existing = self._state.get(username)
            if existing is None:
                self._state[username] = _ChildRateState(
                    capacity=per_minute, window_seconds=60, timestamps=deque()
                )
            else:
                existing.capacity = per_minute

    def consume(self, username: str, *, now: Optional[float] = None) -> bool:
        """Reserve one slot.  Returns True if allowed, False if throttled."""
        username = (username or "").lstrip("@").lower()
        if not username:
            return True
        now = time.monotonic() if now is None else now
        with self._lock:
            state = self._state.get(username)
            if state is None:
                # Default conservative window if we haven't seen the bot yet.
                state = _ChildRateState(capacity=30, window_seconds=60, timestamps=deque())
                self._state[username] = state
            cutoff = now - state.window_seconds
            while state.timestamps and state.timestamps[0] < cutoff:
                state.timestamps.popleft()
            if len(state.timestamps) >= state.capacity:
                return False
            state.timestamps.append(now)
            return True

    def reset(self, username: Optional[str] = None) -> None:
        with self._lock:
            if username is None:
                self._state.clear()
                return
            username = username.lstrip("@").lower()
            self._state.pop(username, None)


_rate_limiter = _RateLimiter()


def check_rate_limit(username: str, *, per_minute: Optional[int] = None) -> bool:
    """Return True if the child can send right now, False if throttled.

    Pass ``per_minute`` from the child's roster entry (or omit to use the
    last-configured value, defaulting to 30/min).
    """
    if per_minute is not None:
        _rate_limiter.configure(username, per_minute=per_minute)
    return _rate_limiter.consume(username)


def reset_rate_limits(username: Optional[str] = None) -> None:
    """Clear rate-limit state.  Test helper."""
    _rate_limiter.reset(username)
