"""Tests for the HKTMemory enterprise knowledge recall provider plugin.

Covers: is_available logic, prefetch serialization, truncation, sync_turn
skip logging, system_prompt_block, register entry point, import-error
handling, and prefetch log verification.
"""

import logging
import os
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from plugins.memory.hktmemory import HKTMemoryProvider, register


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeRecallResult:
    """Mimics hktmemory_provider_hermes.RecallResult for unit tests."""

    def __init__(self, text, source=None, scope=None, confidence=None,
                 timestamp=None, metadata=None):
        self.text = text
        self.source = source
        self.scope = scope
        self.confidence = confidence
        self.timestamp = timestamp
        self.metadata = metadata


class _ProviderCollector:
    """Collects providers registered via ctx.register_memory_provider()."""

    def __init__(self):
        self.providers = []

    def register_memory_provider(self, provider):
        self.providers.append(provider)


# ---------------------------------------------------------------------------
# is_available
# ---------------------------------------------------------------------------


class TestIsAvailable:

    def test_dir_set_cli_present(self, monkeypatch, tmp_path):
        """Available when HERMES_HKTMEMORY_DIR set + hermes-hktmemory on PATH."""
        monkeypatch.setenv("HERMES_HKTMEMORY_DIR", str(tmp_path))
        monkeypatch.setattr(
            "plugins.memory.hktmemory.importlib.util.find_spec",
            lambda name: object() if name == "hktmemory_provider_hermes" else None,
        )
        monkeypatch.setattr(
            "plugins.memory.hktmemory.shutil.which",
            lambda name: "/usr/local/bin/hermes-hktmemory" if name == "hermes-hktmemory" else None,
        )
        p = HKTMemoryProvider()
        assert p.is_available() is True

    def test_dir_set_sqlite_only(self, monkeypatch, tmp_path):
        """Available when HERMES_HKTMEMORY_DIR set, no CLI, but vector_store.db exists."""
        db_path = tmp_path / "vector_store.db"
        db_path.write_text("fake sqlite")
        monkeypatch.setenv("HERMES_HKTMEMORY_DIR", str(tmp_path))
        monkeypatch.setattr(
            "plugins.memory.hktmemory.importlib.util.find_spec",
            lambda name: object() if name == "hktmemory_provider_hermes" else None,
        )
        monkeypatch.setattr("plugins.memory.hktmemory.shutil.which", lambda name: None)
        p = HKTMemoryProvider()
        assert p.is_available() is True

    def test_dir_missing(self, monkeypatch):
        """Not available when HERMES_HKTMEMORY_DIR is not set."""
        monkeypatch.delenv("HERMES_HKTMEMORY_DIR", raising=False)
        p = HKTMemoryProvider()
        assert p.is_available() is False

    def test_dir_set_no_cli_no_db(self, monkeypatch, tmp_path):
        """Not available when dir set but no CLI and no vector_store.db."""
        monkeypatch.setenv("HERMES_HKTMEMORY_DIR", str(tmp_path))
        monkeypatch.setattr(
            "plugins.memory.hktmemory.importlib.util.find_spec",
            lambda name: object() if name == "hktmemory_provider_hermes" else None,
        )
        monkeypatch.setattr("plugins.memory.hktmemory.shutil.which", lambda name: None)
        p = HKTMemoryProvider()
        assert p.is_available() is False

    def test_package_missing(self, monkeypatch, tmp_path):
        """Not available when the adapter package is not importable."""
        monkeypatch.setenv("HERMES_HKTMEMORY_DIR", str(tmp_path))
        monkeypatch.setattr("plugins.memory.hktmemory.importlib.util.find_spec", lambda name: None)
        monkeypatch.setattr(
            "plugins.memory.hktmemory.shutil.which",
            lambda name: "/usr/local/bin/hermes-hktmemory" if name == "hermes-hktmemory" else None,
        )
        p = HKTMemoryProvider()
        assert p.is_available() is False


# ---------------------------------------------------------------------------
# prefetch serialization
# ---------------------------------------------------------------------------


class TestPrefetch:

    def _make_provider_with_mock(self, results):
        """Create a provider with _available=True and mocked _provider."""
        p = HKTMemoryProvider()
        p._available = True
        p._provider = MagicMock()
        p._provider.prefetch.return_value = results
        return p

    def test_prefetch_serializes_results(self, monkeypatch):
        """Fenced output format with source, scope, confidence, text."""
        monkeypatch.setenv("HERMES_HKTMEMORY_DIR", "/data")
        results = [
            _FakeRecallResult(
                text="AI infra deployment uses kustomize",
                source="runbook",
                scope="infra",
                confidence=0.92,
            ),
        ]
        p = self._make_provider_with_mock(results)
        out = p.prefetch("AI infra deployment")

        assert out.startswith("<enterprise-memory>")
        assert out.endswith("</enterprise-memory>")
        assert "source: runbook" in out
        assert "scope: infra" in out
        assert "0.92" in out
        assert "AI infra deployment uses kustomize" in out
        assert "---" in out

    def test_prefetch_empty_query(self, monkeypatch):
        """Empty query returns empty string."""
        monkeypatch.setenv("HERMES_HKTMEMORY_DIR", "/data")
        p = HKTMemoryProvider()
        p._available = True
        p._provider = MagicMock()
        out = p.prefetch("")
        assert out == ""

    def test_prefetch_no_results(self, monkeypatch):
        """Provider returns empty list → empty string."""
        monkeypatch.setenv("HERMES_HKTMEMORY_DIR", "/data")
        p = self._make_provider_with_mock([])
        out = p.prefetch("anything")
        assert out == ""

    def test_prefetch_not_available(self, monkeypatch):
        """Provider not available → empty string."""
        monkeypatch.setenv("HERMES_HKTMEMORY_DIR", "/data")
        p = HKTMemoryProvider()
        p._available = False
        out = p.prefetch("anything")
        assert out == ""

    def test_prefetch_truncates_over_budget(self, monkeypatch):
        """Large result set is truncated at ~8000 chars boundary."""
        monkeypatch.setenv("HERMES_HKTMEMORY_DIR", "/data")
        # Create results that will exceed the 8192 char budget
        big_text = "x" * 2000
        results = [
            _FakeRecallResult(text=big_text, source="s", scope="sc", confidence=0.5)
            for _ in range(5)
        ]
        p = self._make_provider_with_mock(results)
        out = p.prefetch("big query")

        assert "... (truncated)" in out
        assert len(out.encode()) <= 8192

    def test_prefetch_escapes_triple_dash(self, monkeypatch):
        """Literal --- in text is escaped as \\---."""
        monkeypatch.setenv("HERMES_HKTMEMORY_DIR", "/data")
        results = [
            _FakeRecallResult(
                text="some text\n---\nmore text",
                source="doc",
                scope="test",
                confidence=0.8,
            ),
        ]
        p = self._make_provider_with_mock(results)
        out = p.prefetch("test")
        assert "\\---" in out


# ---------------------------------------------------------------------------
# sync_turn (read-only)
# ---------------------------------------------------------------------------


class TestSyncTurn:

    def test_sync_turn_skips_with_reason(self, caplog):
        """sync_turn logs skip reason and does not raise."""
        p = HKTMemoryProvider()
        with caplog.at_level(logging.INFO, logger="plugins.memory.hktmemory"):
            p.sync_turn("user msg", "assistant msg")
        assert "enterprise_memory.capture.skipped" in caplog.text
        assert "read_only" in caplog.text


# ---------------------------------------------------------------------------
# system_prompt_block
# ---------------------------------------------------------------------------


class TestSystemPromptBlock:

    def test_returns_non_empty_fence(self):
        p = HKTMemoryProvider()
        block = p.system_prompt_block()
        assert block
        assert "enterprise-memory" in block


# ---------------------------------------------------------------------------
# register entry point
# ---------------------------------------------------------------------------


class TestRegister:

    def test_register_entry_point(self):
        ctx = _ProviderCollector()
        register(ctx)
        assert len(ctx.providers) == 1
        assert isinstance(ctx.providers[0], HKTMemoryProvider)
        assert ctx.providers[0].name == "hktmemory"


# ---------------------------------------------------------------------------
# initialize import error
# ---------------------------------------------------------------------------


class TestInitialize:

    def test_initialize_import_error(self, monkeypatch):
        """ImportError on hktmemory_provider_hermes sets _available = False."""
        monkeypatch.setenv("HERMES_HKTMEMORY_DIR", "/data")
        monkeypatch.setenv("HERMES_HKTMEMORY_NAMESPACE", "test")
        p = HKTMemoryProvider()
        # The package is not installed in test env, so this naturally fails
        with patch.dict("sys.modules", {"hktmemory_provider_hermes": None}):
            p.initialize("session-1")
        assert p._available is False

    def test_initialize_success(self, monkeypatch):
        """Successful init sets _available = True."""
        monkeypatch.setenv("HERMES_HKTMEMORY_DIR", "/data")
        monkeypatch.setenv("HERMES_HKTMEMORY_NAMESPACE", "test")

        mock_provider_cls = MagicMock()
        mock_instance = MagicMock()
        mock_provider_cls.return_value = mock_instance

        p = HKTMemoryProvider()
        with patch.dict("sys.modules", {"hktmemory_provider_hermes": MagicMock(HKTMemoryHermesProvider=mock_provider_cls)}):
            p.initialize("session-1")

        assert p._available is True
        assert p._provider is mock_instance


# ---------------------------------------------------------------------------
# prefetch logging
# ---------------------------------------------------------------------------


class TestPrefetchLogging:

    def test_prefetch_logs_done_count(self, caplog, monkeypatch):
        """Verify enterprise_memory.recall.done log line includes result count."""
        monkeypatch.setenv("HERMES_HKTMEMORY_DIR", "/data")
        results = [
            _FakeRecallResult(text="hello", source="s", scope="sc", confidence=0.9),
        ]
        p = HKTMemoryProvider()
        p._available = True
        p._provider = MagicMock()
        p._provider.prefetch.return_value = results

        with caplog.at_level(logging.INFO, logger="plugins.memory.hktmemory"):
            p.prefetch("test query")

        assert "enterprise_memory.recall.done" in caplog.text
        assert "results=1" in caplog.text
        assert "emitted=1" in caplog.text


# ---------------------------------------------------------------------------
# get_tool_schemas / shutdown
# ---------------------------------------------------------------------------


class TestToolSchemasAndShutdown:

    def test_get_tool_schemas_empty(self):
        p = HKTMemoryProvider()
        assert p.get_tool_schemas() == []

    def test_shutdown_noop(self):
        p = HKTMemoryProvider()
        p.shutdown()  # should not raise
