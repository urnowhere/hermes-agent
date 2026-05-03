"""Tests for pre_connect hook in MCPServerTask.run() (tools.mcp_tool)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _reset_mcp_state():
    """Ensure clean MCP module state before/after each test."""
    import tools.mcp_tool as mcp

    old_loop = mcp._mcp_loop
    old_thread = mcp._mcp_thread
    old_servers = dict(mcp._servers)
    yield
    mcp._servers.clear()
    mcp._servers.update(old_servers)
    mcp._mcp_loop = old_loop
    mcp._mcp_thread = old_thread


class TestPreConnectHook:
    """Tests for the optional pre_connect key in MCP server config."""

    @pytest.mark.asyncio
    async def test_pre_connect_runs_before_transport(self):
        """pre_connect subprocess runs, then transport proceeds."""
        config = {
            "command": "echo server",
            "pre_connect": "echo prepped",
            "connect_timeout": 1,
        }

        import tools.mcp_tool as mcp

        server = mcp.MCPServerTask("test-preconnect")
        server._reconnect_event.set()
        server._shutdown_event.set()

        with patch.object(
            mcp.asyncio, "create_subprocess_shell"
        ) as mock_subproc:
            mock_proc = MagicMock()
            mock_proc.communicate = AsyncMock(
                return_value=(b"prepped\n", b"")
            )
            mock_proc.returncode = 0
            mock_subproc.return_value = mock_proc

            task = asyncio.ensure_future(server.run(config))
            await asyncio.sleep(0.05)

            mock_subproc.assert_called_once_with(
                "echo prepped",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    @pytest.mark.asyncio
    async def test_pre_connect_failure_is_non_fatal(self):
        """Failed pre_connect should NOT prevent transport from running."""
        config = {
            "command": "echo server",
            "pre_connect": "exit 1",
            "connect_timeout": 1,
        }

        import tools.mcp_tool as mcp

        server = mcp.MCPServerTask("test-preconnect-fail")
        server._reconnect_event.set()
        server._shutdown_event.set()

        with patch.object(
            mcp.asyncio, "create_subprocess_shell"
        ) as mock_subproc:
            mock_proc = MagicMock()
            mock_proc.communicate = AsyncMock(
                return_value=(b"", b"pull failed")
            )
            mock_proc.returncode = 1
            mock_subproc.return_value = mock_proc

            task = asyncio.ensure_future(server.run(config))
            await asyncio.sleep(0.05)

            assert mock_subproc.called, "pre_connect should have been attempted"

        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    @pytest.mark.asyncio
    async def test_no_pre_connect_when_not_configured(self):
        """Without pre_connect key, no subprocess is spawned."""
        config = {"command": "echo server", "connect_timeout": 1}

        import tools.mcp_tool as mcp

        server = mcp.MCPServerTask("test-no-preconnect")
        server._reconnect_event.set()
        server._shutdown_event.set()

        with patch.object(
            mcp.asyncio, "create_subprocess_shell"
        ) as mock_subproc:
            task = asyncio.ensure_future(server.run(config))
            await asyncio.sleep(0.05)

            mock_subproc.assert_not_called()

        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    @pytest.mark.asyncio
    async def test_pre_connect_exception_is_non_fatal(self):
        """OSError in pre_connect subprocess should not crash server."""
        config = {
            "command": "echo server",
            "pre_connect": "docker pull nosuchimage",
            "connect_timeout": 1,
        }

        import tools.mcp_tool as mcp

        server = mcp.MCPServerTask("test-preconnect-exc")
        server._reconnect_event.set()
        server._shutdown_event.set()

        with patch.object(
            mcp.asyncio, "create_subprocess_shell",
            side_effect=OSError("no such file"),
        ) as mock_subproc:
            task = asyncio.ensure_future(server.run(config))
            await asyncio.sleep(0.05)

            assert mock_subproc.called, "pre_connect was attempted despite OSError"

        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    @pytest.mark.asyncio
    async def test_pre_connect_timeout_is_non_fatal(self):
        """Hung pre_connect command should not block server startup."""
        import tools.mcp_tool as mcp

        config = {
            "command": "echo server",
            "pre_connect": "sleep 999",
            "connect_timeout": 0.1,
        }

        server = mcp.MCPServerTask("test-preconnect-timeout")
        server._reconnect_event.set()
        server._shutdown_event.set()

        async def fake_communicate():
            await asyncio.sleep(999)
            return b"", b""

        with patch.object(
            mcp.asyncio, "create_subprocess_shell"
        ) as mock_subproc:
            mock_proc = MagicMock()
            mock_proc.communicate = fake_communicate
            mock_proc.kill = MagicMock()
            mock_subproc.return_value = mock_proc

            task = asyncio.ensure_future(server.run(config))
            await asyncio.sleep(0.2)

            assert mock_subproc.called
            assert mock_proc.kill.called, "proc.kill() should be called on timeout"

        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    @pytest.mark.asyncio
    async def test_non_string_pre_connect_skipped(self):
        """Non-string pre_connect values are silently skipped."""
        import tools.mcp_tool as mcp

        config = {
            "command": "echo server",
            "pre_connect": ["not", "a", "string"],
            "connect_timeout": 1,
        }

        server = mcp.MCPServerTask("test-preconnect-nonstr")
        server._reconnect_event.set()
        server._shutdown_event.set()

        with patch.object(
            mcp.asyncio, "create_subprocess_shell"
        ) as mock_subproc:
            task = asyncio.ensure_future(server.run(config))
            await asyncio.sleep(0.05)

            mock_subproc.assert_not_called()

        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
