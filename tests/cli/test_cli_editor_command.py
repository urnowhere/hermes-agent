"""Tests for the /editor CLI slash command."""

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from cli import HermesCLI


def _make_cli():
    cli_obj = HermesCLI.__new__(HermesCLI)
    cli_obj._app = None
    cli_obj._agent_running = False
    cli_obj._pending_input = MagicMock()
    cli_obj._status_bar_visible = True
    return cli_obj


def _mkstemp_at(path: Path):
    def _factory(*args, **kwargs):
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_TRUNC, 0o600)
        return fd, str(path)

    return _factory


class TestCLIEditorCommand:
    def test_process_command_queues_saved_editor_content(self):
        cli_obj = _make_cli()

        with patch.object(cli_obj, "_open_external_editor", return_value="line 1\nline 2") as mock_open, \
             patch("cli._cprint"):
            assert cli_obj.process_command("/editor") is True

        mock_open.assert_called_once_with(initial_text="")
        cli_obj._pending_input.put.assert_called_once_with("line 1\nline 2")

    def test_edit_alias_dispatches_with_initial_text(self):
        cli_obj = _make_cli()

        with patch.object(cli_obj, "_open_external_editor", return_value="updated body") as mock_open, \
             patch("cli._cprint"):
            assert cli_obj.process_command("/edit Draft title") is True

        mock_open.assert_called_once_with(initial_text="Draft title")
        cli_obj._pending_input.put.assert_called_once_with("updated body")

    def test_process_command_does_not_queue_when_editor_returns_none(self):
        cli_obj = _make_cli()

        with patch.object(cli_obj, "_open_external_editor", return_value=None), \
             patch("cli._cprint"):
            assert cli_obj.process_command("/editor") is True

        cli_obj._pending_input.put.assert_not_called()

    def test_resolve_editor_command_prefers_editor_env_with_args(self):
        cli_obj = _make_cli()

        with patch.dict(os.environ, {"EDITOR": "code --wait"}, clear=False), \
             patch("cli.shutil.which", side_effect=lambda cmd: f"/usr/bin/{cmd}" if cmd == "code" else None):
            assert cli_obj._resolve_editor_command() == ["code", "--wait"]

    def test_resolve_editor_command_falls_back_to_common_editor(self, monkeypatch):
        cli_obj = _make_cli()
        monkeypatch.delenv("EDITOR", raising=False)
        monkeypatch.delenv("VISUAL", raising=False)

        with patch("cli.shutil.which", side_effect=lambda cmd: f"/usr/bin/{cmd}" if cmd == "nano" else None):
            assert cli_obj._resolve_editor_command() == ["nano"]

    def test_open_external_editor_reads_saved_file_and_cleans_up(self, tmp_path):
        cli_obj = _make_cli()
        temp_path = tmp_path / "compose.md"
        seen = {}

        def _fake_subprocess_run(cmd, check=False):
            file_path = Path(cmd[-1])
            seen["initial"] = file_path.read_text(encoding="utf-8")
            file_path.write_text("final body\nwith details", encoding="utf-8")
            return SimpleNamespace(returncode=0)

        with patch.object(cli_obj, "_resolve_editor_command", return_value=["nano"]), \
             patch("cli.tempfile.mkstemp", side_effect=_mkstemp_at(temp_path)), \
             patch("subprocess.run", side_effect=_fake_subprocess_run), \
             patch("cli._cprint"):
            result = cli_obj._open_external_editor(initial_text="draft body")

        assert result == "final body\nwith details"
        assert seen["initial"] == "draft body"
        assert not temp_path.exists()

    def test_open_external_editor_reports_missing_editor(self):
        cli_obj = _make_cli()

        with patch.object(cli_obj, "_resolve_editor_command", return_value=None), \
             patch("cli._cprint") as mock_print:
            result = cli_obj._open_external_editor()

        assert result is None
        rendered = " ".join(str(arg) for call in mock_print.call_args_list for arg in call.args)
        assert "No editor found" in rendered
