"""Tests for cron trigger evaluation — the $0 pre-run gate."""

import os
import sqlite3
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from cron.scheduler import check_trigger


class TestNoTrigger:
    """Jobs without triggers always run."""

    def test_no_trigger_key(self):
        job = {"id": "test1", "name": "test"}
        should_run, reason = check_trigger(job)
        assert should_run is True
        assert reason == "no_trigger"

    def test_none_trigger(self):
        job = {"id": "test2", "name": "test", "trigger": None}
        should_run, reason = check_trigger(job)
        assert should_run is True
        assert reason == "no_trigger"

    def test_unknown_trigger_type(self):
        job = {"id": "test3", "name": "test", "trigger": {"type": "magic"}}
        should_run, reason = check_trigger(job)
        assert should_run is True
        assert "unknown_trigger_type" in reason


class TestSqlTrigger:
    """SQL triggers query state.db and skip if result < threshold."""

    def test_sql_trigger_passes(self):
        """Query returning value >= threshold should allow the job to run."""
        mock_db = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (5,)
        mock_db.conn.execute.return_value = mock_cursor

        job = {
            "id": "sql1", "name": "test",
            "trigger": {"type": "sql", "query": "SELECT 5", "threshold": 1}
        }

        with patch("hermes_state.SessionDB", return_value=mock_db):
            should_run, reason = check_trigger(job)

        assert should_run is True
        assert "sql:5>=1" in reason
        mock_db.close.assert_called_once()

    def test_sql_trigger_blocks(self):
        """Query returning value < threshold should skip the job."""
        mock_db = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (0,)
        mock_db.conn.execute.return_value = mock_cursor

        job = {
            "id": "sql2", "name": "test",
            "trigger": {"type": "sql", "query": "SELECT 0", "threshold": 1}
        }

        with patch("hermes_state.SessionDB", return_value=mock_db):
            should_run, reason = check_trigger(job)

        assert should_run is False
        assert "sql:0<1" in reason

    def test_sql_trigger_null_result(self):
        """Null result should skip the job."""
        mock_db = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = None
        mock_db.conn.execute.return_value = mock_cursor

        job = {
            "id": "sql3", "name": "test",
            "trigger": {"type": "sql", "query": "SELECT NULL"}
        }

        with patch("hermes_state.SessionDB", return_value=mock_db):
            should_run, reason = check_trigger(job)

        assert should_run is False
        assert reason == "null_result"

    def test_sql_trigger_blocks_mutations(self):
        """Mutation queries should be blocked."""
        for keyword in ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE"):
            job = {
                "id": "sql_mut", "name": "test",
                "trigger": {"type": "sql", "query": f"{keyword} INTO foo VALUES(1)"}
            }
            should_run, reason = check_trigger(job)
            assert should_run is False
            assert reason == "blocked_mutation"

    def test_sql_trigger_default_threshold(self):
        """Default threshold should be 1."""
        mock_db = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (1,)
        mock_db.conn.execute.return_value = mock_cursor

        job = {
            "id": "sql4", "name": "test",
            "trigger": {"type": "sql", "query": "SELECT 1"}
        }

        with patch("hermes_state.SessionDB", return_value=mock_db):
            should_run, reason = check_trigger(job)

        assert should_run is True

    def test_sql_trigger_empty_query(self):
        """Empty query should allow the job to run."""
        job = {
            "id": "sql5", "name": "test",
            "trigger": {"type": "sql", "query": ""}
        }
        should_run, reason = check_trigger(job)
        assert should_run is True
        assert reason == "empty_query"

    def test_sql_trigger_error_skips(self):
        """SQL errors should skip the job (fail-closed)."""
        job = {
            "id": "sql6", "name": "test",
            "trigger": {"type": "sql", "query": "SELECT * FROM nonexistent"}
        }

        with patch("hermes_state.SessionDB", side_effect=Exception("no such table")):
            should_run, reason = check_trigger(job)

        assert should_run is False
        assert "sql_error" in reason


class TestFileTrigger:
    """File triggers check mtime against last run."""

    def test_file_changed_since_last_run(self):
        """Modified file should allow the job to run."""
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"test")
            path = f.name

        try:
            # Last run was an hour ago, file was just created
            last_run = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
            job = {
                "id": "file1", "name": "test",
                "last_run_at": last_run,
                "trigger": {"type": "file_changed", "path": path}
            }
            should_run, reason = check_trigger(job)
            assert should_run is True
            assert "file_modified" in reason
        finally:
            os.unlink(path)

    def test_file_unchanged_since_last_run(self):
        """Unchanged file should skip the job."""
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"test")
            path = f.name

        try:
            # Last run is in the future — file hasn't changed since
            last_run = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
            job = {
                "id": "file2", "name": "test",
                "last_run_at": last_run,
                "trigger": {"type": "file_changed", "path": path}
            }
            should_run, reason = check_trigger(job)
            assert should_run is False
            assert reason == "file_unchanged"
        finally:
            os.unlink(path)

    def test_file_first_run(self):
        """First run (no last_run_at) should always run."""
        with tempfile.NamedTemporaryFile(delete=False) as f:
            f.write(b"test")
            path = f.name

        try:
            job = {
                "id": "file3", "name": "test",
                "trigger": {"type": "file_changed", "path": path}
            }
            should_run, reason = check_trigger(job)
            assert should_run is True
            assert reason == "first_run"
        finally:
            os.unlink(path)

    def test_file_not_found(self):
        """Missing file should skip the job."""
        job = {
            "id": "file4", "name": "test",
            "trigger": {"type": "file_changed", "path": "/nonexistent/file.txt"}
        }
        should_run, reason = check_trigger(job)
        assert should_run is False
        assert "file_not_found" in reason


class TestCommandTrigger:
    """Command triggers check exit code."""

    def test_command_success(self):
        """Exit 0 should allow the job to run."""
        job = {
            "id": "cmd1", "name": "test",
            "trigger": {"type": "command", "command": "true"}
        }
        should_run, reason = check_trigger(job)
        assert should_run is True
        assert reason == "command_ok"

    def test_command_failure(self):
        """Non-zero exit should skip the job."""
        job = {
            "id": "cmd2", "name": "test",
            "trigger": {"type": "command", "command": "false"}
        }
        should_run, reason = check_trigger(job)
        assert should_run is False
        assert "command_exit:1" in reason

    def test_command_timeout(self):
        """Commands that exceed 10s timeout should skip."""
        job = {
            "id": "cmd3", "name": "test",
            "trigger": {"type": "command", "command": "sleep 20"}
        }

        import subprocess
        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("sleep", 10)):
            should_run, reason = check_trigger(job)

        assert should_run is False
        assert reason == "command_timeout"

    def test_empty_command(self):
        """Empty command should allow the job to run."""
        job = {
            "id": "cmd4", "name": "test",
            "trigger": {"type": "command", "command": ""}
        }
        should_run, reason = check_trigger(job)
        assert should_run is True
        assert reason == "no_command"
