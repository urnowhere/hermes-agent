"""Shared state helpers for the Hermes Web Console backend."""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from .event_bus import GuiEventBus

MAX_TRACKED_RUNS = 200
TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled"}


@dataclass(slots=True)
class WebConsoleState:
    """Container for shared web console backend primitives."""

    event_bus: GuiEventBus = field(default_factory=GuiEventBus)
    runs: OrderedDict[str, dict[str, Any]] = field(default_factory=OrderedDict)

    def _evict_runs_if_needed(self) -> None:
        """Evict oldest terminal runs first, then oldest active runs if still over limit."""
        while len(self.runs) > MAX_TRACKED_RUNS:
            evicted = False
            for existing_run_id, existing_run in list(self.runs.items()):
                if existing_run.get("status") in TERMINAL_RUN_STATUSES:
                    del self.runs[existing_run_id]
                    evicted = True
                    break
            if evicted:
                continue
            self.runs.popitem(last=False)

    def record_run(self, run_id: str, metadata: dict[str, Any]) -> dict[str, Any]:
        """Store or replace the tracked metadata for a run."""
        self.runs[run_id] = dict(metadata)
        self.runs.move_to_end(run_id)
        self._evict_runs_if_needed()
        return self.runs[run_id]

    def update_run(self, run_id: str, **metadata: Any) -> dict[str, Any] | None:
        """Update tracked metadata for a run if it exists."""
        run = self.runs.get(run_id)
        if run is None:
            return None
        run.update(metadata)
        self.runs.move_to_end(run_id)
        self._evict_runs_if_needed()
        return run

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        """Return tracked metadata for a run if it exists."""
        run = self.runs.get(run_id)
        if run is None:
            return None
        return dict(run)


_shared_state: WebConsoleState | None = None


def create_web_console_state() -> WebConsoleState:
    """Create an isolated web console state container."""
    return WebConsoleState()


def get_web_console_state() -> WebConsoleState:
    """Return the shared web console state container."""
    global _shared_state

    if _shared_state is None:
        _shared_state = create_web_console_state()
    return _shared_state
