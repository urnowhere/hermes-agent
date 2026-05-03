from types import SimpleNamespace
from unittest.mock import patch

import pytest

from hermes_cli.config import (
    format_managed_message,
    get_managed_system,
    is_managed,
    recommended_update_command,
)
from hermes_cli.main import cmd_update
from tools.skills_hub import OptionalSkillSource


def test_get_managed_system_homebrew(monkeypatch):
    monkeypatch.setenv("HERMES_MANAGED", "homebrew")

    assert get_managed_system() == "Homebrew"
    assert recommended_update_command() == "brew upgrade hermes-agent"


def test_format_managed_message_homebrew(monkeypatch):
    monkeypatch.setenv("HERMES_MANAGED", "homebrew")

    message = format_managed_message("update Hermes Agent")

    assert "managed by Homebrew" in message
    assert "brew upgrade hermes-agent" in message


def test_recommended_update_command_defaults_to_hermes_update(monkeypatch):
    monkeypatch.delenv("HERMES_MANAGED", raising=False)

    assert recommended_update_command() == "hermes update"


def test_cmd_update_blocks_managed_homebrew(monkeypatch, capsys):
    monkeypatch.setenv("HERMES_MANAGED", "homebrew")

    with patch("hermes_cli.main.subprocess.run") as mock_run:
        cmd_update(SimpleNamespace())

    assert not mock_run.called
    captured = capsys.readouterr()
    assert "managed by Homebrew" in captured.err
    assert "brew upgrade hermes-agent" in captured.err


@pytest.mark.parametrize("falsey_value", ["false", "False", "FALSE", "0", "no", "off", "OFF"])
def test_get_managed_system_returns_none_for_falsey_values(monkeypatch, falsey_value):
    """Falsey env values like 'false', '0', 'no', 'off' should disable managed mode.

    Regression test for https://github.com/NousResearch/hermes-agent/issues/12864
    """
    monkeypatch.setenv("HERMES_MANAGED", falsey_value)

    assert get_managed_system() is None
    assert is_managed() is False
    assert recommended_update_command() == "hermes update"


def test_format_managed_message_not_triggered_for_false(monkeypatch):
    """format_managed_message should not be reachable when HERMES_MANAGED=false.

    Regression test for https://github.com/NousResearch/hermes-agent/issues/12864
    """
    monkeypatch.setenv("HERMES_MANAGED", "false")

    # is_managed() must be False, so managed-guarded code paths never execute
    assert is_managed() is False
    assert get_managed_system() is None
    # Verify update command falls back to the default unmanaged path
    assert recommended_update_command() == "hermes update"


def test_optional_skill_source_honors_env_override(monkeypatch, tmp_path):
    optional_dir = tmp_path / "optional-skills"
    optional_dir.mkdir()
    monkeypatch.setenv("HERMES_OPTIONAL_SKILLS", str(optional_dir))

    source = OptionalSkillSource()

    assert source._optional_dir == optional_dir
