"""Workspace file watcher — optional daemon that polls workspace roots for changes
and triggers knowledgebase re-indexing with debounce.

This module is intentionally NOT imported at module level in hot paths.
Import it lazily only when the watcher feature is enabled.
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Re-use the debounce constant from workspace module
from agent.workspace import _AUTO_INDEX_DEBOUNCE_SECONDS


class WorkspaceWatcher:
    """Polls workspace roots for file mtime changes and triggers re-indexing.

    Parameters
    ----------
    config : dict
        Full Hermes config dict.  The knowledgebase section is consulted for
        ``watch_interval_seconds`` (default 10).
    debounce_seconds : float | None
        Override for the debounce window.  When *None* the module-level
        ``_AUTO_INDEX_DEBOUNCE_SECONDS`` (30 s) is used.
    """

    def __init__(
        self,
        config: dict[str, Any],
        debounce_seconds: float | None = None,
    ) -> None:
        self._config = config
        kb_cfg = (config.get("knowledgebase") or {})
        self._interval: float = float(kb_cfg.get("watch_interval_seconds", 10))
        self._debounce: float = (
            debounce_seconds if debounce_seconds is not None else _AUTO_INDEX_DEBOUNCE_SECONDS
        )

        # mtime tracking: absolute path str -> last seen mtime (float)
        self._mtimes: dict[str, float] = {}

        # Thread control
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_index_time: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        """Start the background polling thread (daemon)."""
        if self.is_running:
            logger.debug("WorkspaceWatcher already running — ignoring start()")
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True, name="workspace-watcher")
        self._thread.start()
        logger.debug("WorkspaceWatcher started (interval=%.1fs, debounce=%.1fs)",
                      self._interval, self._debounce)

    def stop(self) -> None:
        """Signal the polling thread to stop and wait for it to exit."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=self._interval + 2)
            self._thread = None
        logger.debug("WorkspaceWatcher stopped")

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _run(self) -> None:
        """Main loop executed on the daemon thread."""
        while not self._stop_event.is_set():
            try:
                self._poll_once()
            except Exception:
                logger.debug("WorkspaceWatcher poll error", exc_info=True)
            # Sleep in small increments so stop() is responsive
            self._stop_event.wait(timeout=self._interval)

    def _poll_once(self) -> None:
        """Scan all workspace root files, compare mtimes, trigger re-index if changed."""
        from agent.workspace import get_workspace_root_specs, _iter_root_files

        logger.debug("WorkspaceWatcher polling workspace roots")

        changed = False
        current_paths: set[str] = set()

        for root_spec in get_workspace_root_specs(self._config):
            if not root_spec.root_path.exists():
                continue
            for file_path in _iter_root_files(root_spec, self._config):
                abs_key = str(file_path)
                current_paths.add(abs_key)
                try:
                    mtime = file_path.stat().st_mtime
                except OSError:
                    continue
                prev = self._mtimes.get(abs_key)
                if prev is None:
                    # First time seeing this file — record but don't trigger
                    # (avoids spurious re-index on first poll)
                    self._mtimes[abs_key] = mtime
                elif mtime != prev:
                    self._mtimes[abs_key] = mtime
                    changed = True

        # Detect deletions
        deleted = set(self._mtimes) - current_paths
        if deleted:
            for key in deleted:
                del self._mtimes[key]
            changed = True

        if changed:
            self._maybe_reindex()

    def _maybe_reindex(self) -> None:
        """Trigger re-indexing if enough time has passed since the last run."""
        now = time.monotonic()
        elapsed = now - self._last_index_time
        if elapsed < self._debounce:
            logger.debug(
                "WorkspaceWatcher change detected but debounce active (%.1fs remaining)",
                self._debounce - elapsed,
            )
            return

        logger.info("WorkspaceWatcher: file changes detected — triggering re-index")
        self._last_index_time = now

        from agent.workspace import index_workspace_knowledgebase

        try:
            index_workspace_knowledgebase(config=self._config)
        except Exception:
            logger.warning("WorkspaceWatcher: re-index failed", exc_info=True)
