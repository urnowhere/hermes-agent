"""Tests for HermesCLI /profile slash-command behavior."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from cli import HermesCLI


def _make_cli():
    cli_obj = HermesCLI.__new__(HermesCLI)
    cli_obj.console = MagicMock()
    cli_obj.config = {}
    cli_obj._pending_input = MagicMock()
    cli_obj._app = None
    return cli_obj


class TestProfileSlashCommand:
    def test_no_arg_profile_uses_active_profile_helpers(self, capsys):
        cli_obj = _make_cli()

        with patch("hermes_cli.profiles.get_active_profile_name", return_value="custom"), \
             patch("hermes_constants.display_hermes_home", return_value="/tmp/hermes-home"):
            assert cli_obj.process_command("/profile") is True

        output = capsys.readouterr().out
        assert "Profile: custom" in output
        assert "Home:    /tmp/hermes-home" in output
        assert "default" not in output

    def test_profile_list_marks_active_profile(self, capsys):
        cli_obj = _make_cli()
        profiles = [
            SimpleNamespace(name="default", is_default=True),
            SimpleNamespace(name="coder", is_default=False),
            SimpleNamespace(name="turing", is_default=False),
        ]

        with patch("hermes_cli.profiles.get_active_profile_name", return_value="coder"), \
             patch("hermes_cli.profiles.list_profiles", return_value=profiles):
            assert cli_obj.process_command("/profile list") is True

        output = capsys.readouterr().out
        assert "Active profile: coder" in output
        assert "Profiles:" in output
        assert "* coder" in output
        assert "default (default)" in output
        assert "turing" in output

    @patch("cli.os.execvp")
    @patch("cli.shutil.which", return_value="/usr/local/bin/hermes")
    def test_profile_use_switches_and_reexecs_with_hermes_shim(self, mock_which, mock_execvp, capsys):
        cli_obj = _make_cli()

        with patch("hermes_cli.profiles.get_active_profile_name", return_value="default"), \
             patch("hermes_cli.profiles.set_active_profile") as mock_set_active:
            assert cli_obj.process_command("/profile use coder") is True

        output = capsys.readouterr().out
        assert "Switching to profile: coder" in output
        mock_set_active.assert_called_once_with("coder")
        mock_execvp.assert_called_once_with("/usr/local/bin/hermes", ["hermes", "--profile", "coder"])

    @patch("cli.os.execvp")
    @patch("cli.shutil.which", return_value=None)
    def test_profile_name_alias_switches_via_python_module_fallback(self, mock_which, mock_execvp, capsys):
        cli_obj = _make_cli()

        with patch("cli.sys.executable", "/usr/bin/python3"), \
             patch("hermes_cli.profiles.get_active_profile_name", return_value="default"), \
             patch("hermes_cli.profiles.set_active_profile") as mock_set_active:
            assert cli_obj.process_command("/profile coder") is True

        output = capsys.readouterr().out
        assert "Switching to profile: coder" in output
        mock_set_active.assert_called_once_with("coder")
        mock_execvp.assert_called_once_with(
            "/usr/bin/python3",
            ["/usr/bin/python3", "-m", "hermes_cli.main", "--profile", "coder"],
        )

    @patch("cli.os.execvp")
    @patch("cli.shutil.which", return_value="/usr/local/bin/hermes")
    def test_same_profile_is_a_noop(self, mock_which, mock_execvp, capsys):
        cli_obj = _make_cli()

        with patch("hermes_cli.profiles.get_active_profile_name", return_value="coder"), \
             patch("hermes_cli.profiles.set_active_profile") as mock_set_active:
            assert cli_obj.process_command("/profile coder") is True

        output = capsys.readouterr().out
        assert "Already using profile: coder" in output
        mock_set_active.assert_not_called()
        mock_execvp.assert_not_called()

    @patch("cli.os.execvp")
    @patch("cli.shutil.which", return_value="/usr/local/bin/hermes")
    def test_profile_use_missing_name_prints_help_and_does_not_restart(self, mock_which, mock_execvp, capsys):
        cli_obj = _make_cli()

        with patch("hermes_cli.profiles.get_active_profile_name", return_value="default"), \
             patch("hermes_cli.profiles.set_active_profile") as mock_set_active:
            assert cli_obj.process_command("/profile use") is True

        output = capsys.readouterr().out
        assert "Usage: /profile [list|use <name>|<name>]" in output
        mock_set_active.assert_not_called()
        mock_execvp.assert_not_called()

    @patch("cli.os.execvp")
    @patch("cli.shutil.which", return_value="/usr/local/bin/hermes")
    def test_profile_list_extra_args_prints_help_and_does_not_restart(self, mock_which, mock_execvp, capsys):
        cli_obj = _make_cli()

        with patch("hermes_cli.profiles.get_active_profile_name", return_value="default"), \
             patch("hermes_cli.profiles.set_active_profile") as mock_set_active:
            assert cli_obj.process_command("/profile list extra") is True

        output = capsys.readouterr().out
        assert "Usage: /profile [list|use <name>|<name>]" in output
        mock_set_active.assert_not_called()
        mock_execvp.assert_not_called()

    @patch("cli.os.execvp")
    @patch("cli.shutil.which", return_value="/usr/local/bin/hermes")
    def test_profile_use_reports_profile_errors_without_restart(self, mock_which, mock_execvp, capsys):
        cli_obj = _make_cli()

        with patch("hermes_cli.profiles.get_active_profile_name", return_value="default"), \
             patch("hermes_cli.profiles.set_active_profile", side_effect=FileNotFoundError("Profile 'ghost' does not exist.")) as mock_set_active:
            assert cli_obj.process_command("/profile use ghost") is True

        output = capsys.readouterr().out
        assert "Could not switch profile" in output
        mock_set_active.assert_called_once_with("ghost")
        mock_execvp.assert_not_called()
