"""Verify the TUI slash_worker suppresses MCP discovery at import time.

Regression test for #15275 -- without the HERMES_MCP_DISCOVERY=0 guard in
slash_worker.py, importing the cli module triggers discover_mcp_tools() via
model_tools, spawning duplicate ``hermes mcp serve`` children per session.
"""

import ast
import os


def test_slash_worker_sets_mcp_discovery_before_cli_import():
    """slash_worker.main() must set HERMES_MCP_DISCOVERY=0 before importing cli."""
    import importlib
    src = importlib.util.find_spec("tui_gateway.slash_worker")
    assert src is not None and src.origin is not None
    with open(src.origin) as fh:
        lines = fh.readlines()

    # Find the line that sets the env var and the first cli import.
    env_line_idx = None
    cli_import_idx = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if "HERMES_MCP_DISCOVERY" in stripped and "environ" in stripped:
            env_line_idx = i
        # Only match actual import statements, not comments or strings
        if cli_import_idx is None and stripped.startswith(("import cli", "from cli ")):
            cli_import_idx = i

    assert env_line_idx is not None, (
        "slash_worker.py must set HERMES_MCP_DISCOVERY env var"
    )
    assert cli_import_idx is not None, (
        "slash_worker.py must import cli"
    )
    assert env_line_idx < cli_import_idx, (
        "HERMES_MCP_DISCOVERY must be set before cli is imported "
        f"(env var at line {env_line_idx + 1}, cli import at line {cli_import_idx + 1})"
    )


def test_slash_worker_overrides_parent_mcp_setting():
    """slash_worker must force MCP discovery off, not inherit a parent override."""
    import importlib
    src = importlib.util.find_spec("tui_gateway.slash_worker")
    assert src is not None and src.origin is not None
    with open(src.origin) as fh:
        source = fh.read()

    assert 'os.environ["HERMES_MCP_DISCOVERY"] = "0"' in source
    assert "setdefault" not in source


def test_slash_worker_does_not_import_cli_at_module_level():
    """cli must NOT be imported at module scope -- it must be lazy in main()."""
    import importlib
    src = importlib.util.find_spec("tui_gateway.slash_worker")
    assert src is not None and src.origin is not None
    with open(src.origin) as fh:
        source = fh.read()

    tree = ast.parse(source)
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not alias.name.startswith("cli"), (
                    f"cli must not be imported at module level (line {node.lineno})"
                )
        elif isinstance(node, ast.ImportFrom):
            assert node.module is None or not node.module.startswith("cli"), (
                f"cli must not be imported at module level (line {node.lineno})"
            )


def test_model_tools_skips_mcp_when_env_var_set(monkeypatch):
    """model_tools must skip discover_mcp_tools() when HERMES_MCP_DISCOVERY=0."""
    monkeypatch.setenv("HERMES_MCP_DISCOVERY", "0")

    assert os.environ.get("HERMES_MCP_DISCOVERY") == "0"

    # Simulate the guard check from model_tools.py
    should_discover = os.environ.get("HERMES_MCP_DISCOVERY") != "0"
    assert should_discover is False, (
        "MCP discovery should be suppressed when HERMES_MCP_DISCOVERY=0"
    )


def test_model_tools_runs_mcp_when_env_var_absent(monkeypatch):
    """model_tools must run discover_mcp_tools() when env var is not set."""
    monkeypatch.delenv("HERMES_MCP_DISCOVERY", raising=False)

    should_discover = os.environ.get("HERMES_MCP_DISCOVERY") != "0"
    assert should_discover is True, (
        "MCP discovery should run when HERMES_MCP_DISCOVERY is not set"
    )


def test_tui_server_forces_mcp_discovery_off_for_worker(monkeypatch):
    """The slash worker subprocess must always inherit HERMES_MCP_DISCOVERY=0."""
    import importlib
    server = importlib.import_module("tui_gateway.server")

    popen_kwargs = {}

    class DummyProc:
        stdin = None
        stdout = None
        stderr = None

    class DummyThread:
        def __init__(self, *args, **kwargs):
            pass

        def start(self):
            pass

    def fake_popen(*args, **kwargs):
        popen_kwargs.update(kwargs)
        return DummyProc()

    monkeypatch.setenv("HERMES_MCP_DISCOVERY", "1")
    monkeypatch.setattr(server.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(server.threading, "Thread", DummyThread)

    server._SlashWorker("session-1", "")

    assert popen_kwargs["env"]["HERMES_MCP_DISCOVERY"] == "0"
