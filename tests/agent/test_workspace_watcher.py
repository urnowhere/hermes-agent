"""Tests for agent.workspace_watcher.WorkspaceWatcher."""
from __future__ import annotations

import time
import threading
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from agent.workspace_watcher import WorkspaceWatcher, _AUTO_INDEX_DEBOUNCE_SECONDS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(interval: float = 0.1, **kb_overrides: Any) -> dict[str, Any]:
    """Return a minimal config dict with a fast poll interval for tests."""
    kb: dict[str, Any] = {
        "watch_interval_seconds": interval,
        "watch_for_changes": True,
    }
    kb.update(kb_overrides)
    return {
        "workspace": {"enabled": True, "path": ""},
        "knowledgebase": kb,
    }


# Patch targets: because _poll_once does `from agent.workspace import ...`
# we must patch at the source module so the lazy import picks up our mock.
_PATCH_ROOT_SPECS = "agent.workspace.get_workspace_root_specs"
_PATCH_ITER_FILES = "agent.workspace._iter_root_files"
_PATCH_INDEX = "agent.workspace.index_workspace_knowledgebase"


# ---------------------------------------------------------------------------
# Lifecycle tests
# ---------------------------------------------------------------------------

class TestStartStopLifecycle:
    def test_start_sets_running(self):
        watcher = WorkspaceWatcher(_make_config(), debounce_seconds=0)
        assert not watcher.is_running
        with patch(_PATCH_ROOT_SPECS, return_value=[]), \
             patch(_PATCH_ITER_FILES, return_value=[]):
            watcher.start()
            assert watcher.is_running
            watcher.stop()
            assert not watcher.is_running

    def test_double_start_is_safe(self):
        watcher = WorkspaceWatcher(_make_config(), debounce_seconds=0)
        with patch(_PATCH_ROOT_SPECS, return_value=[]), \
             patch(_PATCH_ITER_FILES, return_value=[]):
            watcher.start()
            watcher.start()  # should not raise or create a second thread
            assert watcher.is_running
            watcher.stop()

    def test_stop_without_start_is_safe(self):
        watcher = WorkspaceWatcher(_make_config())
        watcher.stop()  # no-op
        assert not watcher.is_running

    def test_stop_actually_stops_thread(self):
        watcher = WorkspaceWatcher(_make_config(interval=0.05), debounce_seconds=0)
        with patch(_PATCH_ROOT_SPECS, return_value=[]), \
             patch(_PATCH_ITER_FILES, return_value=[]):
            watcher.start()
            thread = watcher._thread
            assert thread is not None
            assert thread.is_alive()
            watcher.stop()
            # Thread should have exited
            assert not thread.is_alive()
            assert watcher._thread is None


# ---------------------------------------------------------------------------
# Change detection tests
# ---------------------------------------------------------------------------

class TestChangeDetection:
    def test_file_change_triggers_reindex(self, tmp_path: Path):
        """When a tracked file's mtime changes, index_workspace_knowledgebase is called."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello")

        from agent.workspace import WorkspaceRootSpec
        spec = WorkspaceRootSpec(label="workspace", root_path=tmp_path, recursive=True, is_workspace=True)

        config = _make_config(interval=0.05)
        watcher = WorkspaceWatcher(config, debounce_seconds=0)

        mock_index = MagicMock(return_value={"success": True})

        with patch(_PATCH_ROOT_SPECS, return_value=[spec]), \
             patch(_PATCH_ITER_FILES, return_value=[test_file]):
            # First poll — seeds mtimes, no re-index expected
            watcher._poll_once()
            assert str(test_file) in watcher._mtimes

            # Simulate mtime change
            time.sleep(0.05)
            test_file.write_text("world")

            with patch(_PATCH_INDEX, mock_index):
                watcher._poll_once()

        mock_index.assert_called_once_with(config=config)

    def test_file_deletion_triggers_reindex(self, tmp_path: Path):
        """When a previously tracked file disappears, re-index is triggered."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("data")

        from agent.workspace import WorkspaceRootSpec
        spec = WorkspaceRootSpec(label="workspace", root_path=tmp_path, recursive=True, is_workspace=True)
        config = _make_config(interval=0.05)
        watcher = WorkspaceWatcher(config, debounce_seconds=0)

        mock_index = MagicMock(return_value={"success": True})

        with patch(_PATCH_ROOT_SPECS, return_value=[spec]), \
             patch(_PATCH_ITER_FILES, return_value=[test_file]):
            watcher._poll_once()  # seed

        # Now file is "gone" — iter returns nothing
        with patch(_PATCH_ROOT_SPECS, return_value=[spec]), \
             patch(_PATCH_ITER_FILES, return_value=[]), \
             patch(_PATCH_INDEX, mock_index):
            watcher._poll_once()

        mock_index.assert_called_once()

    def test_no_change_no_reindex(self, tmp_path: Path):
        """When nothing changes between polls, no re-index fires."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("stable")

        from agent.workspace import WorkspaceRootSpec
        spec = WorkspaceRootSpec(label="workspace", root_path=tmp_path, recursive=True, is_workspace=True)
        config = _make_config(interval=0.05)
        watcher = WorkspaceWatcher(config, debounce_seconds=0)

        mock_index = MagicMock(return_value={"success": True})

        with patch(_PATCH_ROOT_SPECS, return_value=[spec]), \
             patch(_PATCH_ITER_FILES, return_value=[test_file]), \
             patch(_PATCH_INDEX, mock_index):
            watcher._poll_once()  # seed
            watcher._poll_once()  # second poll, nothing changed

        mock_index.assert_not_called()


# ---------------------------------------------------------------------------
# Debounce tests
# ---------------------------------------------------------------------------

class TestDebounce:
    def test_debounce_prevents_rapid_reindex(self, tmp_path: Path):
        """Two rapid changes should only trigger one re-index within the debounce window."""
        file_a = tmp_path / "a.txt"
        file_a.write_text("v1")

        from agent.workspace import WorkspaceRootSpec
        spec = WorkspaceRootSpec(label="workspace", root_path=tmp_path, recursive=True, is_workspace=True)
        config = _make_config(interval=0.05)
        # Use a long debounce so second poll is still within window
        watcher = WorkspaceWatcher(config, debounce_seconds=60)

        mock_index = MagicMock(return_value={"success": True})

        with patch(_PATCH_ROOT_SPECS, return_value=[spec]), \
             patch(_PATCH_ITER_FILES, return_value=[file_a]):
            watcher._poll_once()  # seed

        # First change
        time.sleep(0.05)
        file_a.write_text("v2")
        with patch(_PATCH_ROOT_SPECS, return_value=[spec]), \
             patch(_PATCH_ITER_FILES, return_value=[file_a]), \
             patch(_PATCH_INDEX, mock_index):
            watcher._poll_once()
        assert mock_index.call_count == 1

        # Second change — should be debounced
        time.sleep(0.05)
        file_a.write_text("v3")
        with patch(_PATCH_ROOT_SPECS, return_value=[spec]), \
             patch(_PATCH_ITER_FILES, return_value=[file_a]), \
             patch(_PATCH_INDEX, mock_index):
            watcher._poll_once()
        # Still only one call — second was debounced
        assert mock_index.call_count == 1

    def test_reindex_after_debounce_expires(self, tmp_path: Path):
        """After the debounce window expires, a new change should trigger re-index."""
        file_a = tmp_path / "a.txt"
        file_a.write_text("v1")

        from agent.workspace import WorkspaceRootSpec
        spec = WorkspaceRootSpec(label="workspace", root_path=tmp_path, recursive=True, is_workspace=True)
        config = _make_config(interval=0.05)
        # Very short debounce for testability
        watcher = WorkspaceWatcher(config, debounce_seconds=0.1)

        mock_index = MagicMock(return_value={"success": True})

        with patch(_PATCH_ROOT_SPECS, return_value=[spec]), \
             patch(_PATCH_ITER_FILES, return_value=[file_a]):
            watcher._poll_once()  # seed

        # First change
        time.sleep(0.05)
        file_a.write_text("v2")
        with patch(_PATCH_ROOT_SPECS, return_value=[spec]), \
             patch(_PATCH_ITER_FILES, return_value=[file_a]), \
             patch(_PATCH_INDEX, mock_index):
            watcher._poll_once()
        assert mock_index.call_count == 1

        # Wait for debounce to expire
        time.sleep(0.15)

        # Second change — debounce expired so should fire
        file_a.write_text("v3")
        with patch(_PATCH_ROOT_SPECS, return_value=[spec]), \
             patch(_PATCH_ITER_FILES, return_value=[file_a]), \
             patch(_PATCH_INDEX, mock_index):
            watcher._poll_once()
        assert mock_index.call_count == 2
