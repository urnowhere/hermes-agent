"""Tests for CLI background task state, artifacts, and /bg subcommands."""

import json
import threading
from datetime import datetime
from unittest.mock import MagicMock, patch

from cli import HermesCLI
from hermes_cli.commands import SUBCOMMANDS


def _make_cli():
    cli_obj = HermesCLI.__new__(HermesCLI)
    cli_obj.config = {}
    cli_obj.console = MagicMock()
    cli_obj.agent = None
    cli_obj.conversation_history = []
    cli_obj.session_id = "session-123"
    cli_obj._pending_input = MagicMock()
    cli_obj._agent_running = False
    cli_obj._background_tasks = {}
    cli_obj._background_task_counter = 0
    cli_obj._background_task_state = {}
    cli_obj._background_task_lock = threading.Lock()
    cli_obj._pending_background_context_notes = []
    cli_obj._session_db = MagicMock()
    cli_obj.model = "openai/gpt-5.4"
    cli_obj.session_start = datetime(2026, 4, 12, 12, 0)
    return cli_obj


def test_bg_alias_has_registered_subcommands():
    assert "/bg" in SUBCOMMANDS
    assert SUBCOMMANDS["/bg"] == ["status", "log", "files", "kill"]


def test_process_command_bg_status_dispatches_namespace_handler():
    cli_obj = _make_cli()

    with patch.object(cli_obj, "_handle_background_status_command") as mock_status, \
         patch.object(cli_obj, "_handle_background_command") as mock_background:
        assert cli_obj.process_command("/bg status") is True

    mock_status.assert_called_once_with(None)
    mock_background.assert_not_called()


def test_process_command_background_prompt_still_runs_background_handler():
    cli_obj = _make_cli()

    with patch.object(cli_obj, "_handle_background_command") as mock_background:
        assert cli_obj.process_command("/bg build a site") is True

    mock_background.assert_called_once_with("/bg build a site")


def test_background_context_notes_defer_until_foreground_turn_finishes():
    cli_obj = _make_cli()
    cli_obj._agent_running = True

    cli_obj._queue_background_context_note("Background task complete.")
    assert cli_obj.conversation_history == []

    cli_obj._agent_running = False
    cli_obj._flush_pending_background_context_notes()

    assert cli_obj.conversation_history == [
        {"role": "user", "content": "[SYSTEM: Background task complete.]"}
    ]
    cli_obj._session_db.append_message.assert_called_once()


def test_background_manifest_persists_profile_safe_artifacts(tmp_path, monkeypatch):
    hermes_home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    cli_obj = _make_cli()

    task_id = "bg_test"
    artifact_dir = hermes_home / "background_tasks" / task_id
    with cli_obj._background_task_lock:
        cli_obj._background_task_state[task_id] = {
            "task_id": task_id,
            "task_num": 1,
            "parent_session_id": "session-123",
            "background_session_id": task_id,
            "status": "running",
            "prompt": "test prompt",
            "prompt_preview": "test prompt",
            "cwd": str(tmp_path),
            "artifact_dir": str(artifact_dir),
            "started_at": "2026-04-12T12:00:00",
            "finished_at": None,
            "last_activity_at": "2026-04-12T12:00:01",
            "current_activity": "Running terminal",
            "current_tool": "terminal",
            "summary_preview": "",
            "error": None,
            "changes": {"created": ["app.py"], "modified": [], "deleted": []},
            "changed_files": ["app.py"],
            "session_log_path": str(hermes_home / "sessions" / "session_bg_test.json"),
        }

    cli_obj._persist_background_task_manifest(task_id)

    manifest = json.loads((artifact_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["task_id"] == task_id
    assert manifest["change_counts"]["created"] == 1
    assert manifest["artifact_dir"] == str(artifact_dir)


def test_background_status_lists_live_activity(tmp_path, monkeypatch):
    hermes_home = tmp_path / ".hermes"
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    cli_obj = _make_cli()

    task_id = "bg_live"
    artifact_dir = hermes_home / "background_tasks" / task_id
    with cli_obj._background_task_lock:
        cli_obj._background_task_state[task_id] = {
            "task_id": task_id,
            "task_num": 2,
            "parent_session_id": "session-123",
            "background_session_id": task_id,
            "status": "running",
            "prompt": "ship it",
            "prompt_preview": "ship it",
            "cwd": str(tmp_path),
            "artifact_dir": str(artifact_dir),
            "started_at": "2026-04-12T12:00:00",
            "finished_at": None,
            "last_activity_at": "2026-04-12T12:00:02",
            "current_activity": "Running write_file",
            "current_tool": "write_file",
            "summary_preview": "",
            "error": None,
            "changes": {"created": [], "modified": [], "deleted": []},
            "changed_files": [],
            "session_log_path": "",
        }

    cli_obj._handle_background_status_command(None)

    printed = cli_obj.console.print.call_args[0][0]
    assert "Background Tasks" in printed
    assert task_id in printed
    assert "Running write_file" in printed
