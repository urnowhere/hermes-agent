import argparse
from types import SimpleNamespace

import pytest

from plugins.memory.keep.cli import cmd_status, keep_command, register_cli


def test_register_cli_builds_subcommand_tree():
    parser = argparse.ArgumentParser()
    register_cli(parser)

    args = parser.parse_args(["status"])
    assert args.keep_cli_command == "status"

    with pytest.raises(SystemExit):
        parser.parse_args(["setup"])

def test_keep_command_defaults_to_status(monkeypatch):
    called = {}

    def fake_status(args):
        called["args"] = args

    monkeypatch.setattr("plugins.memory.keep.cli.cmd_status", fake_status)

    args = SimpleNamespace(keep_cli_command=None)
    keep_command(args)

    assert called["args"] is args


def test_keep_status_prints_minimal_fields(monkeypatch, capsys):
    monkeypatch.setattr("hermes_cli.profiles.get_active_profile_name", lambda: "coder")
    monkeypatch.setattr("plugins.memory.keep.cli._store_path", lambda: "/tmp/hermes/keep")
    monkeypatch.setattr("plugins.memory.keep.cli._display_path", lambda path: str(path))
    monkeypatch.setattr("plugins.memory.keep.cli._config_state", lambda path: "configured")
    monkeypatch.setattr("plugins.memory.keep.cli._daemon_state", lambda path: "running")

    cmd_status(SimpleNamespace())

    out = capsys.readouterr().out
    assert "Profile:      coder" in out
    assert "Store path:   /tmp/hermes/keep" in out
    assert "Config state: configured" in out
    assert "Daemon state: running" in out
