import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from tools.profile_delegation_tool import (
    _build_profile_prompt,
    _resolve_profile_home,
    _resolve_timeout,
    ask_profile,
)


def _profile_home(tmp_path: Path) -> Path:
    home = tmp_path / "expert-home"
    home.mkdir()
    (home / "config.yaml").write_text("model:\n  default: test\n", encoding="utf-8")
    return home


def test_resolves_configured_profile_home(tmp_path):
    home = _profile_home(tmp_path)
    cfg = {"profile_experts": {"architect": {"hermes_home": str(home)}}}

    assert _resolve_profile_home("architect", cfg) == home.resolve()


def test_rejects_unconfigured_profile_when_named_profiles_disabled():
    cfg = {"allow_named_profile_experts": False}

    try:
        _resolve_profile_home("missing", cfg)
    except ValueError as exc:
        assert "not allowlisted" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_timeout_is_capped():
    cfg = {"profile_timeout_seconds": 10, "profile_max_timeout_seconds": 30}

    assert _resolve_timeout("architect", 999, cfg) == 30
    assert _resolve_timeout("architect", None, cfg) == 10


def test_prompt_contains_evidence_contract():
    prompt = _build_profile_prompt("architect", "Review this.", "Extra context")

    assert "specialist Hermes profile" in prompt
    assert "Separate verified facts from assumptions" in prompt
    assert "Extra context" in prompt


@patch("tools.profile_delegation_tool.subprocess.run")
@patch("tools.profile_delegation_tool._load_delegation_config")
def test_ask_profile_invokes_hermes_subprocess(mock_load_config, mock_run, tmp_path):
    home = _profile_home(tmp_path)
    mock_load_config.return_value = {
        "profile_experts": {
            "architect": {
                "hermes_home": str(home),
                "toolsets": ["skills", "mcp-qmd"],
                "timeout_seconds": 12,
            }
        }
    }
    mock_run.return_value = subprocess.CompletedProcess(
        args=["hermes"],
        returncode=0,
        stdout="expert answer\n",
        stderr="",
    )

    result = json.loads(ask_profile("architect", "Review this."))

    assert result["status"] == "completed"
    assert result["profile"] == "architect"
    assert result["output"] == "expert answer"
    _, kwargs = mock_run.call_args
    assert kwargs["env"]["HERMES_HOME"] == str(home.resolve())
    assert kwargs["timeout"] == 12
    command = mock_run.call_args.args[0]
    assert command[1:3] == ["-m", "hermes_cli.main"]
    assert "--toolsets" in command
    assert "skills,mcp-qmd" in command


@patch("tools.profile_delegation_tool.subprocess.run")
@patch("tools.profile_delegation_tool._load_delegation_config")
def test_ask_profile_reports_timeout(mock_load_config, mock_run, tmp_path):
    home = _profile_home(tmp_path)
    mock_load_config.return_value = {
        "profile_experts": {"architect": {"hermes_home": str(home)}},
        "profile_timeout_seconds": 5,
    }
    mock_run.side_effect = subprocess.TimeoutExpired(
        cmd=["hermes"],
        timeout=5,
        output="partial",
        stderr="slow",
    )

    result = json.loads(ask_profile("architect", "Review this."))

    assert result["status"] == "timed_out"
    assert result["exit_code"] == 124
    assert result["output"] == "partial"
    assert result["error"] == "slow"
