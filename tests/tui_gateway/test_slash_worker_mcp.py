"""Tests for lazy MCP connection pool approach to #15275.

Verifies that MCP tool discovery is deferred until first tool use
(lazy initialization), preventing duplicate subprocess spawning at
import time.  Inspired by Claude Code's SocketPool.ensureConnected().
"""

import json
import os
import sys
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_mcp_state():
    """Reset MCP module-level state between tests."""
    import tools.mcp_tool as mcp_mod
    prev_initialized = mcp_mod._mcp_initialized
    prev_servers = dict(mcp_mod._servers)
    mcp_mod._mcp_initialized = False
    mcp_mod._servers.clear()
    yield
    mcp_mod._mcp_initialized = prev_initialized
    mcp_mod._servers.clear()
    mcp_mod._servers.update(prev_servers)


# ---------------------------------------------------------------------------
# Tests: discover_mcp_tools(lazy=True) defers connections
# ---------------------------------------------------------------------------

class TestLazyMCPDiscovery:

    def test_lazy_mode_does_not_connect_at_call_time(self):
        """discover_mcp_tools(lazy=True) should NOT call register_mcp_servers."""
        import tools.mcp_tool as mcp_mod

        with patch.object(mcp_mod, "register_mcp_servers", return_value=[]) as mock_reg:
            with patch.object(mcp_mod, "_load_mcp_config", return_value={
                "test_server": {"command": "echo", "args": ["hello"]}
            }):
                with patch.object(mcp_mod, "_load_cached_tool_definitions", return_value=None):
                    result = mcp_mod.discover_mcp_tools(lazy=True)

        # register_mcp_servers should NOT be called in lazy mode
        mock_reg.assert_not_called()

    def test_lazy_mode_returns_empty_when_no_cache(self):
        """When no cache exists and lazy=True, should return empty list."""
        import tools.mcp_tool as mcp_mod

        with patch.object(mcp_mod, "_load_mcp_config", return_value={
            "test_server": {"command": "echo"}
        }):
            with patch.object(mcp_mod, "_load_cached_tool_definitions", return_value=None):
                result = mcp_mod.discover_mcp_tools(lazy=True)

        assert result == []

    def test_lazy_mode_registers_stubs_from_cache(self):
        """When cache exists and lazy=True, should register tool stubs."""
        import tools.mcp_tool as mcp_mod
        from tools import registry

        cached_tools = [
            {
                "name": "search",
                "description": "Search for things",
                "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}},
            }
        ]

        with patch.object(mcp_mod, "_load_mcp_config", return_value={
            "test_server": {"command": "echo", "timeout": 30}
        }):
            with patch.object(mcp_mod, "_load_cached_tool_definitions", return_value=cached_tools):
                result = mcp_mod.discover_mcp_tools(lazy=True)

        assert "mcp__test_server__search" in result

    def test_lazy_mode_sets_initialized_false(self):
        """After lazy discovery, _mcp_initialized should still be False."""
        import tools.mcp_tool as mcp_mod

        with patch.object(mcp_mod, "_load_mcp_config", return_value={
            "test_server": {"command": "echo"}
        }):
            with patch.object(mcp_mod, "_load_cached_tool_definitions", return_value=None):
                mcp_mod.discover_mcp_tools(lazy=True)

        assert mcp_mod._mcp_initialized is False


# ---------------------------------------------------------------------------
# Tests: ensure_mcp_initialized() triggers actual connection
# ---------------------------------------------------------------------------

class TestEnsureMCPInitialized:

    def test_ensure_connects_on_first_call(self):
        """ensure_mcp_initialized() should call register_mcp_servers."""
        import tools.mcp_tool as mcp_mod

        with patch.object(mcp_mod, "register_mcp_servers", return_value=[]) as mock_reg:
            with patch.object(mcp_mod, "_load_mcp_config", return_value={
                "test_server": {"command": "echo"}
            }):
                with patch.object(mcp_mod, "_save_cached_tool_definitions"):
                    mcp_mod.ensure_mcp_initialized()

        mock_reg.assert_called_once()
        assert mcp_mod._mcp_initialized is True

    def test_ensure_is_noop_after_first_call(self):
        """Subsequent calls to ensure_mcp_initialized() should be no-ops."""
        import tools.mcp_tool as mcp_mod

        with patch.object(mcp_mod, "register_mcp_servers", return_value=[]) as mock_reg:
            with patch.object(mcp_mod, "_load_mcp_config", return_value={
                "test_server": {"command": "echo"}
            }):
                with patch.object(mcp_mod, "_save_cached_tool_definitions"):
                    mcp_mod.ensure_mcp_initialized()
                    mcp_mod.ensure_mcp_initialized()
                    mcp_mod.ensure_mcp_initialized()

        # Should only be called once despite 3 ensure calls
        mock_reg.assert_called_once()

    def test_ensure_is_noop_when_no_servers_configured(self):
        """When no MCP servers configured, ensure should be safe no-op."""
        import tools.mcp_tool as mcp_mod

        with patch.object(mcp_mod, "_load_mcp_config", return_value={}):
            mcp_mod.ensure_mcp_initialized()

        assert mcp_mod._mcp_initialized is True

    def test_ensure_sets_initialized_flag(self):
        """After ensure_mcp_initialized(), _mcp_initialized should be True."""
        import tools.mcp_tool as mcp_mod

        with patch.object(mcp_mod, "_load_mcp_config", return_value={}):
            mcp_mod.ensure_mcp_initialized()

        assert mcp_mod._mcp_initialized is True


# ---------------------------------------------------------------------------
# Tests: tool cache persistence
# ---------------------------------------------------------------------------

class TestMCPCache:

    def test_save_and_load_cache(self):
        """Cached tool definitions should persist and reload correctly."""
        import tools.mcp_tool as mcp_mod
        import hermes_constants

        tools_data = [
            {"name": "search", "description": "Search", "inputSchema": {"type": "object"}},
            {"name": "read", "description": "Read", "inputSchema": {"type": "object"}},
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(hermes_constants, "get_hermes_home", return_value=tmpdir):
                mcp_mod._save_cached_tool_definitions("test_server", tools_data)
                loaded = mcp_mod._load_cached_tool_definitions("test_server")

        assert loaded is not None
        assert len(loaded) == 2
        assert loaded[0]["name"] == "search"
        assert loaded[1]["name"] == "read"

    def test_load_cache_returns_none_when_missing(self):
        """Missing cache should return None."""
        import tools.mcp_tool as mcp_mod
        import hermes_constants

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(hermes_constants, "get_hermes_home", return_value=tmpdir):
                result = mcp_mod._load_cached_tool_definitions("nonexistent")

        assert result is None


# ---------------------------------------------------------------------------
# Tests: slash_worker imports model_tools without spawning MCP servers
# ---------------------------------------------------------------------------

class TestSlashWorkerImportSafety:

    def test_importing_model_tools_does_not_spawn_mcp(self):
        """Importing model_tools with lazy MCP should not trigger connections.

        This is the core fix for #15275: slash_worker imports cli which
        imports model_tools.  With lazy=True, no MCP subprocesses spawn
        at import time.
        """
        import tools.mcp_tool as mcp_mod

        # Simulate what model_tools.py does at import time
        with patch.object(mcp_mod, "register_mcp_servers", return_value=[]) as mock_reg:
            with patch.object(mcp_mod, "_load_mcp_config", return_value={
                "test_server": {"command": "echo"}
            }):
                with patch.object(mcp_mod, "_load_cached_tool_definitions", return_value=None):
                    mcp_mod.discover_mcp_tools(lazy=True)

        # The critical assertion: register_mcp_servers was NOT called
        mock_reg.assert_not_called()
        assert mcp_mod._mcp_initialized is False

    def test_subprocess_import_does_not_spawn_mcp_children(self):
        """Real subprocess test: importing model_tools should not spawn
        MCP server subprocesses (the core #15275 issue)."""
        code = (
            "import tools.mcp_tool as mcp\n"
            "original = mcp.register_mcp_servers\n"
            "called = []\n"
            "def spy(*a, **kw):\n"
            "    called.append(True)\n"
            "    return original(*a, **kw)\n"
            "mcp.register_mcp_servers = spy\n"
            "import model_tools\n"
            "print('CALLED' if called else 'SKIPPED')\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, timeout=30,
            cwd=str(_PROJECT_ROOT),
        )
        assert result.returncode == 0, f"Subprocess failed: {result.stderr}"
        assert "SKIPPED" in result.stdout, (
            f"register_mcp_servers was called at import time despite lazy=True. "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
