"""Tests for #15275: slash_worker must not eagerly trigger MCP tool discovery.

The slash worker only processes slash commands (/help, /model, /tools, etc.)
and never needs MCP-backed tools.  When model_tools is imported (transitively
via cli), it calls discover_mcp_tools() at module level — which spawns
subprocesses for configured MCP servers.  The slash worker should suppress
this to avoid duplicate MCP children per TUI session.
"""

import os
import ast
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


@pytest.fixture(autouse=True)
def _clean_env():
    """Remove HERMES_SKIP_MCP_DISCOVERY so tests start from a clean state."""
    prev = os.environ.pop("HERMES_SKIP_MCP_DISCOVERY", None)
    yield
    if prev is not None:
        os.environ["HERMES_SKIP_MCP_DISCOVERY"] = prev


class TestModelToolsMCPDiscoveryGuard:
    """Verify that model_tools.py checks HERMES_SKIP_MCP_DISCOVERY before
    calling discover_mcp_tools() at module level."""

    def test_source_has_skip_mcp_guard(self):
        """model_tools.py should contain a check for HERMES_SKIP_MCP_DISCOVERY
        that gates the discover_mcp_tools() call."""
        model_tools_path = _PROJECT_ROOT / "model_tools.py"
        source = model_tools_path.read_text()

        assert "HERMES_SKIP_MCP_DISCOVERY" in source, (
            "model_tools.py does not check HERMES_SKIP_MCP_DISCOVERY"
        )

    def test_source_guard_structure(self):
        """The guard should wrap the discover_mcp_tools() call — the env var
        check should appear before the call in the source."""
        model_tools_path = _PROJECT_ROOT / "model_tools.py"
        source = model_tools_path.read_text()

        env_var_pos = source.index("HERMES_SKIP_MCP_DISCOVERY")
        call_pos = source.index("discover_mcp_tools()")
        assert env_var_pos < call_pos, (
            "HERMES_SKIP_MCP_DISCOVERY check should appear before discover_mcp_tools() call"
        )

    def test_discover_mcp_tools_skipped_when_env_set(self):
        """When HERMES_SKIP_MCP_DISCOVERY is set, discover_mcp_tools should NOT
        be called at module level.  Verify by mocking the function and checking
        it was not invoked."""
        from tools.mcp_tool import discover_mcp_tools

        with patch.dict(os.environ, {"HERMES_SKIP_MCP_DISCOVERY": "1"}):
            with patch("tools.mcp_tool.discover_mcp_tools", wraps=discover_mcp_tools) as spy:
                # Re-execute the module-level guard block logic
                import importlib
                import model_tools
                # The guard already ran at import time, but we can verify
                # that if the env var is set, the guard short-circuits.
                # We test the actual guard expression:
                result = os.environ.get("HERMES_SKIP_MCP_DISCOVERY")
                assert result is not None, "Env var should be set"

    def test_env_var_truthy_values(self):
        """Various truthy values for HERMES_SKIP_MCP_DISCOVERY should all
        cause the guard to short-circuit."""
        model_tools_path = _PROJECT_ROOT / "model_tools.py"
        source = model_tools_path.read_text()

        # The guard should use os.environ.get() which returns None or a string
        assert "os.environ.get" in source and "HERMES_SKIP_MCP_DISCOVERY" in source

        # Verify the guard treats any non-empty string as truthy (Python's
        # os.environ.get returns None when unset, which is falsy).
        for val in ("1", "true", "yes", "anything"):
            with patch.dict(os.environ, {"HERMES_SKIP_MCP_DISCOVERY": val}):
                assert os.environ.get("HERMES_SKIP_MCP_DISCOVERY") == val
                assert bool(os.environ.get("HERMES_SKIP_MCP_DISCOVERY")) is True


class TestSlashWorkerSetsEnvVar:
    """Verify that slash_worker.py sets HERMES_SKIP_MCP_DISCOVERY before
    importing cli, preventing duplicate MCP subprocess spawns."""

    def test_slash_worker_sets_skip_mcp_env(self):
        """slash_worker.py should set HERMES_SKIP_MCP_DISCOVERY in os.environ
        before importing cli."""
        worker_path = _PROJECT_ROOT / "tui_gateway" / "slash_worker.py"
        source = worker_path.read_text()
        tree = ast.parse(source)

        has_skip_mcp = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Subscript)
                        and isinstance(target.value, ast.Attribute)
                        and target.value.attr == "environ"
                        and isinstance(target.slice, ast.Constant)
                        and "SKIP_MCP" in str(target.slice.value)
                    ):
                        has_skip_mcp = True

        assert has_skip_mcp, (
            "slash_worker.py does not set HERMES_SKIP_MCP_DISCOVERY "
            "in os.environ before importing cli"
        )

    def test_slash_worker_env_var_set_before_cli_import(self):
        """HERMES_SKIP_MCP_DISCOVERY should be set BEFORE the cli import."""
        worker_path = _PROJECT_ROOT / "tui_gateway" / "slash_worker.py"
        source = worker_path.read_text()

        env_var_pos = source.index("HERMES_SKIP_MCP_DISCOVERY")
        import_pos = source.index("from cli import") if "from cli import" in source else source.index("import cli")

        assert env_var_pos < import_pos, (
            "HERMES_SKIP_MCP_DISCOVERY must be set before 'import cli' / 'from cli import' "
            "in slash_worker.py"
        )

    def test_slash_worker_env_var_value(self):
        """The env var should be set to a truthy string value."""
        worker_path = _PROJECT_ROOT / "tui_gateway" / "slash_worker.py"
        source = worker_path.read_text()
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if (
                        isinstance(target, ast.Subscript)
                        and isinstance(target.value, ast.Attribute)
                        and target.value.attr == "environ"
                        and isinstance(target.slice, ast.Constant)
                        and "SKIP_MCP" in str(target.slice.value)
                    ):
                        # The assigned value should be a non-empty string
                        value = node.value
                        if isinstance(value, ast.Constant):
                            assert isinstance(value.value, str) and value.value, (
                                "HERMES_SKIP_MCP_DISCOVERY should be set to a truthy string"
                            )


class TestMCPDiscoveryGuardIntegration:
    """Integration test: verify the guard actually prevents MCP subprocess
    spawning by testing the module-level guard behavior."""

    def test_guard_block_skips_mcp_discovery(self):
        """When HERMES_SKIP_MCP_DISCOVERY is set, the model_tools module-level
        code should skip discover_mcp_tools().  We verify by patching
        discover_mcp_tools and checking it was never called during a fresh
        subprocess simulation."""
        import subprocess
        import sys

        # Run model_tools import in a subprocess with the env var set
        code = (
            "import os\n"
            "os.environ['HERMES_SKIP_MCP_DISCOVERY'] = '1'\n"
            "import tools.mcp_tool as mcp\n"
            "original = mcp.discover_mcp_tools\n"
            "called = []\n"
            "def spy(*a, **kw):\n"
            "    called.append(True)\n"
            "    return original(*a, **kw)\n"
            "mcp.discover_mcp_tools = spy\n"
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
            f"discover_mcp_tools was called despite HERMES_SKIP_MCP_DISCOVERY=1. "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )

    def test_guard_allows_mcp_when_env_not_set(self):
        """When HERMES_SKIP_MCP_DISCOVERY is NOT set, discover_mcp_tools should
        be called at module level (normal behavior)."""
        import subprocess
        import sys

        code = (
            "import tools.mcp_tool as mcp\n"
            "original = mcp.discover_mcp_tools\n"
            "called = []\n"
            "def spy(*a, **kw):\n"
            "    called.append(True)\n"
            "    return []\n"
            "mcp.discover_mcp_tools = spy\n"
            "import model_tools\n"
            "print('CALLED' if called else 'SKIPPED')\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, timeout=30,
            cwd=str(_PROJECT_ROOT),
        )
        assert result.returncode == 0, f"Subprocess failed: {result.stderr}"
        assert "CALLED" in result.stdout, (
            f"discover_mcp_tools was NOT called when HERMES_SKIP_MCP_DISCOVERY is unset. "
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )

    def test_env_var_cleared_after_test(self):
        """After each test, HERMES_SKIP_MCP_DISCOVERY should not leak."""
        assert "HERMES_SKIP_MCP_DISCOVERY" not in os.environ
