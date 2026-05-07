"""Tests for hermes_cli.auto_update module."""

import json
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def test_parse_check_interval_shortcuts():
    """Verify that interval shortcuts parse to correct second values."""
    from hermes_cli.auto_update import parse_check_interval

    assert parse_check_interval("1h") == 3600
    assert parse_check_interval("6h") == 21600
    assert parse_check_interval("12h") == 43200
    assert parse_check_interval("24h") == 86400
    assert parse_check_interval("48h") == 172800
    assert parse_check_interval("72h") == 259200
    assert parse_check_interval("invalid") is None
    assert parse_check_interval("") is None


def test_auto_updater_init():
    """AutoUpdater initializes with correct config values."""
    from hermes_cli.auto_update import AutoUpdater

    # Default config
    updater = AutoUpdater({})
    assert updater.enabled is False
    assert updater.mode == "notify"
    assert updater.check_interval_seconds == 86400  # 24h default
    assert updater.grace_period_seconds == 300

    # Custom config
    config = {
        "auto_update": {
            "enabled": True,
            "mode": "apply",
            "check_interval": "6h",
            "grace_period_seconds": 600,
        }
    }
    updater = AutoUpdater(config)
    assert updater.enabled is True
    assert updater.mode == "apply"
    assert updater.check_interval_seconds == 21600
    assert updater.grace_period_seconds == 600


def test_auto_updater_should_apply_skips_when_disabled():
    """should_apply returns False when auto_update is disabled."""
    from hermes_cli.auto_update import AutoUpdater

    updater = AutoUpdater({"auto_update": {"enabled": False}})
    assert updater.should_apply() is False


def test_auto_updater_should_apply_skips_notify_mode():
    """should_apply returns False when mode is 'notify'."""
    from hermes_cli.auto_update import AutoUpdater

    updater = AutoUpdater({
        "auto_update": {"enabled": True, "mode": "notify"}
    })
    assert updater.should_apply() is False


def test_home_channel_persistence(tmp_path, monkeypatch):
    """Home channel is persisted to and loaded from disk correctly."""
    from hermes_cli.auto_update import AutoUpdater, _HOME_CHANNEL_FILE

    # Create a fake hermes home
    fake_home = tmp_path / ".hermes"
    fake_home.mkdir()

    monkeypatch.setenv("HERMES_HOME", str(fake_home))

    updater = AutoUpdater({})

    # Initially no home channel
    assert updater._load_home_channel() is None

    # Save a home channel
    channel = {"platform": "telegram", "chat_id": "12345"}
    updater._save_home_channel(channel)

    # Verify file was created
    channel_file = fake_home / _HOME_CHANNEL_FILE
    assert channel_file.exists()

    # Load it back
    loaded = updater._load_home_channel()
    assert loaded == channel

    # update_home_channel is a convenience method
    updater.update_home_channel("discord", "67890")
    loaded = updater._load_home_channel()
    assert loaded == {"platform": "discord", "chat_id": "67890"}


def test_is_fork_detection(tmp_path, monkeypatch):
    """Fork detection returns True for non-official repos."""
    from hermes_cli.auto_update import AutoUpdater

    # Create a fake git repo
    repo_dir = tmp_path / "hermes-agent"
    repo_dir.mkdir()
    (repo_dir / ".git").mkdir()

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    # Mock git remote get-url origin to return a fork URL
    mock_result = MagicMock(returncode=0, stdout="https://github.com/forker/hermes-agent.git\n")

    with patch("subprocess.run", return_value=mock_result):
        updater = AutoUpdater({})
        is_fork = updater._is_fork()
        assert is_fork is True


def test_is_fork_detection_official_repo(tmp_path, monkeypatch):
    """Fork detection returns False for the official NousResearch repo."""
    from hermes_cli.auto_update import AutoUpdater

    # Create a fake git repo
    repo_dir = tmp_path / "hermes-agent"
    repo_dir.mkdir()
    (repo_dir / ".git").mkdir()

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    # Mock git remote get-url origin to return official URL
    mock_result = MagicMock(returncode=0, stdout="https://github.com/NousResearch/hermes-agent.git\n")

    with patch("subprocess.run", return_value=mock_result):
        updater = AutoUpdater({})
        is_fork = updater._is_fork()
        assert is_fork is False


def test_check_for_updates_returns_correct_structure(tmp_path, monkeypatch):
    """check_for_updates returns a dict with available, version, commits."""
    from hermes_cli.auto_update import AutoUpdater

    repo_dir = tmp_path / "hermes-agent"
    repo_dir.mkdir()
    (repo_dir / ".git").mkdir()

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    # Mock git commands: fork check (fails since no real remote), fetch, rev-list
    def mock_run(cmd, *args, **kwargs):
        if "remote" in cmd and "get-url" in cmd:
            return MagicMock(returncode=0, stdout="https://github.com/NousResearch/hermes-agent.git\n")
        if "fetch" in cmd:
            return MagicMock(returncode=0, stdout="")
        if "rev-list" in cmd:
            return MagicMock(returncode=0, stdout="5\n")
        if "describe" in cmd:
            return MagicMock(returncode=0, stdout="v1.2.3-10-gabcdef\n")
        return MagicMock(returncode=1, stdout="")

    with patch("subprocess.run", side_effect=mock_run):
        updater = AutoUpdater({})
        result = updater.check_for_updates()

    assert result["available"] is True
    assert result["commits"] == 5


def test_should_apply_respects_grace_period(tmp_path, monkeypatch):
    """should_apply returns False if grace period hasn't passed."""
    from hermes_cli.auto_update import AutoUpdater
    from datetime import datetime, timezone

    repo_dir = tmp_path / "hermes-agent"
    repo_dir.mkdir()
    (repo_dir / ".git").mkdir()

    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    def mock_run(cmd, *args, **kwargs):
        if "remote" in cmd and "get-url" in cmd:
            return MagicMock(returncode=0, stdout="https://github.com/NousResearch/hermes-agent.git\n")
        if "fetch" in cmd:
            return MagicMock(returncode=0, stdout="")
        if "rev-list" in cmd:
            return MagicMock(returncode=0, stdout="5\n")
        if "describe" in cmd:
            return MagicMock(returncode=0, stdout="v1.2.3\n")
        return MagicMock(returncode=1, stdout="")

    config = {
        "auto_update": {
            "enabled": True,
            "mode": "apply",
            "check_interval": "24h",
            "grace_period_seconds": 3600,  # 1 hour grace period
        }
    }

    with patch("subprocess.run", side_effect=mock_run):
        updater = AutoUpdater(config)

        # First call should apply (no previous apply)
        # But let's set _last_apply_ts to recent to trigger grace period
        updater._last_apply_ts = datetime.now(timezone.utc).timestamp() - 60  # 60 seconds ago

        result = updater.should_apply()
        assert result is False  # Grace period not passed
