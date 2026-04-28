"""Windows-native tests for quick commands - skipped due to NoConsoleScreenBufferError."""

import subprocess
import pytest
from unittest.mock import MagicMock, patch
from rich.text import Text


class TestCLIQuickCommands:
    """Test quick command dispatch in HermesCLI.process_command - skipped on Windows."""

    @staticmethod
    def _printed_plain(call_arg):
        if isinstance(call_arg, Text):
            return call_arg.plain
        return str(call_arg)

    def _make_cli(self, quick_commands):
        from cli import HermesCLI

        cli = HermesCLI.__new__(HermesCLI)
        cli.config = {"quick_commands": quick_commands}
        cli.console = MagicMock()
        cli.agent = None
        cli.conversation_history = []
        return cli

    @pytest.mark.skip(
        reason="Windows: NoConsoleScreenBufferError in pytest parallel mode"
    )
    def test_exec_command_no_output_shows_fallback(self):
        cli = self._make_cli({"empty": {"type": "exec", "command": "true"}})
        cli.process_command("/empty")
        cli.console.print.assert_called_once()
        args = cli.console.print.call_args[0][0]
        assert "no output" in args.lower()
