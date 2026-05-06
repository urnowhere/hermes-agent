"""Tests for BaseEnvironment RTK command rewriting.

Covers the integration between BaseEnvironment._prepare_command() and
tools.rtk_manager, verifying config-gated activation and graceful fallback.
"""

import os
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

from tools.environments.base import BaseEnvironment


class _TestableEnv(BaseEnvironment):
    """Concrete subclass for testing base class methods."""

    def __init__(self, cwd="/tmp", timeout=10):
        super().__init__(cwd=cwd, timeout=timeout)

    def _run_bash(self, cmd_string, *, login=False, timeout=120, stdin_data=None):
        raise NotImplementedError("Use mock")

    def cleanup(self):
        pass


def _write_config(tmp_path, config):
    """Write a config.yaml to the mocked HERMES_HOME."""
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(config))


class TestRtkRewrite:
    """Tests for RTK integration in BaseEnvironment._prepare_command()."""

    def test_rtk_disabled_by_default(self):
        """When rtk_integration is not configured, command passes through unchanged."""
        env = _TestableEnv()
        result = env._prepare_command("git status")
        assert result == ("git status", None)

    def test_rtk_enabled_rewrites_command(self, tmp_path):
        """When rtk_integration=true, known commands are rewritten."""
        fake_rtk = tmp_path / "rtk"
        fake_rtk.write_text(
            "#!/bin/sh\n"
            'if [ "$1" = "rewrite" ] && [ "$2" = "git status" ]; then\n'
            '  echo "rtk git status"\n'
            "  exit 3\n"
            "fi\n"
            "exit 1\n"
        )
        fake_rtk.chmod(0o755)

        _write_config(
            tmp_path,
            {"terminal": {"rtk_integration": True, "rtk_auto_download": False}},
        )

        env = _TestableEnv()
        with (
            patch("tools.rtk_manager.shutil.which", return_value=str(fake_rtk)),
            patch("tools.rtk_manager._verify_binary", return_value=True),
            patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}),
        ):
            result = env._prepare_command("git status")
            assert result == ("rtk git status", None)

    def test_rtk_enabled_unknown_command_passthrough(self, tmp_path):
        """When rtk_integration=true, unknown commands pass through unchanged."""
        fake_rtk = tmp_path / "rtk"
        fake_rtk.write_text("#!/bin/sh\nexit 1\n")
        fake_rtk.chmod(0o755)

        _write_config(
            tmp_path,
            {"terminal": {"rtk_integration": True, "rtk_auto_download": False}},
        )

        env = _TestableEnv()
        with (
            patch("tools.rtk_manager.shutil.which", return_value=str(fake_rtk)),
            patch("tools.rtk_manager._verify_binary", return_value=True),
            patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}),
        ):
            result = env._prepare_command("echo hello")
            assert result == ("echo hello", None)

    def test_rtk_missing_binary_graceful_fallback(self, tmp_path):
        """When RTK is enabled but binary is missing, command passes through."""
        _write_config(
            tmp_path,
            {"terminal": {"rtk_integration": True, "rtk_auto_download": False}},
        )

        env = _TestableEnv()
        with (
            patch("tools.rtk_manager.shutil.which", return_value=None),
            patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}),
        ):
            result = env._prepare_command("git status")
            assert result == ("git status", None)

    def test_rtk_auto_download_disabled(self, tmp_path):
        """When auto_download is false and RTK missing, skip without downloading."""
        _write_config(
            tmp_path,
            {"terminal": {"rtk_integration": True, "rtk_auto_download": False}},
        )

        env = _TestableEnv()
        with (
            patch("tools.rtk_manager.shutil.which", return_value=None),
            patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}),
            patch("tools.rtk_manager._download_and_extract") as mock_download,
        ):
            result = env._prepare_command("git status")
            assert result == ("git status", None)
            mock_download.assert_not_called()

    def test_sudo_still_works_with_rtk(self, tmp_path):
        """RTK rewrite happens before sudo transformation."""
        fake_rtk = tmp_path / "rtk"
        fake_rtk.write_text(
            "#!/bin/sh\n"
            'if [ "$1" = "rewrite" ] && [ "$2" = "sudo git status" ]; then\n'
            '  echo "rtk sudo git status"\n'
            "  exit 3\n"
            "fi\n"
            "exit 1\n"
        )
        fake_rtk.chmod(0o755)

        _write_config(
            tmp_path,
            {"terminal": {"rtk_integration": True, "rtk_auto_download": False}},
        )

        env = _TestableEnv()
        with (
            patch("tools.rtk_manager.shutil.which", return_value=str(fake_rtk)),
            patch("tools.rtk_manager._verify_binary", return_value=True),
            patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}),
            patch(
                "tools.terminal_tool._transform_sudo_command",
                return_value=("rtk sudo -S git status", "secret\n"),
            ),
        ):
            result = env._prepare_command("sudo git status")
            # Should be rewritten to rtk sudo git status, then sudo-transformed
            assert result[0].startswith("rtk sudo")
            assert result[1] is not None  # sudo_stdin should be set

    def test_compound_command_rewrite(self, tmp_path):
        """RTK can rewrite compound commands (git status && git log)."""
        fake_rtk = tmp_path / "rtk"
        fake_rtk.write_text(
            "#!/bin/sh\n"
            'if [ "$1" = "rewrite" ]; then\n'
            '  echo "rtk $2"\n'
            "  exit 3\n"
            "fi\n"
            "exit 1\n"
        )
        fake_rtk.chmod(0o755)

        _write_config(
            tmp_path,
            {"terminal": {"rtk_integration": True, "rtk_auto_download": False}},
        )

        env = _TestableEnv()
        with (
            patch("tools.rtk_manager.shutil.which", return_value=str(fake_rtk)),
            patch("tools.rtk_manager._verify_binary", return_value=True),
            patch.dict(os.environ, {"HERMES_HOME": str(tmp_path)}),
        ):
            result = env._prepare_command("git status && git log")
            assert result[0] == "rtk git status && git log"
