"""Tests for rl_training_tool.py — file handle lifecycle and cleanup.

Verifies that _stop_training_run properly closes log file handles,
terminates processes, and handles edge cases on failure paths.
Inspired by PR #715 (0xbyt4).
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tools.rl_training_tool import RunState, _stop_training_run, _spawn_training_run


def _make_run_state(**overrides) -> RunState:
    """Create a minimal RunState for testing."""
    defaults = {
        "run_id": "test-run-001",
        "environment": "test_env",
        "config": {},
    }
    defaults.update(overrides)
    return RunState(**defaults)


class TestStopTrainingRunFileHandles:
    """Verify that _stop_training_run closes log file handles stored as attributes."""

    def test_closes_all_log_file_handles(self):
        state = _make_run_state()
        files = {}
        for attr in ("api_log_file", "trainer_log_file", "env_log_file"):
            fh = MagicMock()
            setattr(state, attr, fh)
            files[attr] = fh

        _stop_training_run(state)

        for attr, fh in files.items():
            fh.close.assert_called_once()
            assert getattr(state, attr) is None

    def test_clears_file_attrs_to_none(self):
        state = _make_run_state()
        state.api_log_file = MagicMock()

        _stop_training_run(state)

        assert state.api_log_file is None

    def test_close_exception_does_not_propagate(self):
        """If a file handle .close() raises, it must not crash."""
        state = _make_run_state()
        bad_fh = MagicMock()
        bad_fh.close.side_effect = OSError("already closed")
        good_fh = MagicMock()
        state.api_log_file = bad_fh
        state.trainer_log_file = good_fh

        _stop_training_run(state)  # should not raise

        bad_fh.close.assert_called_once()
        good_fh.close.assert_called_once()

    def test_handles_missing_file_attrs(self):
        """RunState without log file attrs should not crash."""
        state = _make_run_state()
        # No log file attrs set at all — getattr(..., None) should handle it
        _stop_training_run(state)  # should not raise


class TestStopTrainingRunProcesses:
    """Verify that _stop_training_run terminates processes correctly."""

    def test_terminates_running_processes(self):
        state = _make_run_state()
        for attr in ("api_process", "trainer_process", "env_process"):
            proc = MagicMock()
            proc.poll.return_value = None  # still running
            setattr(state, attr, proc)

        _stop_training_run(state)

        for attr in ("api_process", "trainer_process", "env_process"):
            getattr(state, attr).terminate.assert_called_once()

    def test_does_not_terminate_exited_processes(self):
        state = _make_run_state()
        proc = MagicMock()
        proc.poll.return_value = 0  # already exited
        state.api_process = proc

        _stop_training_run(state)

        proc.terminate.assert_not_called()

    def test_handles_none_processes(self):
        state = _make_run_state()
        # All process attrs are None by default
        _stop_training_run(state)  # should not raise

    def test_handles_mixed_running_and_exited_processes(self):
        state = _make_run_state()
        # api still running
        api = MagicMock()
        api.poll.return_value = None
        state.api_process = api
        # trainer already exited
        trainer = MagicMock()
        trainer.poll.return_value = 0
        state.trainer_process = trainer
        # env is None
        state.env_process = None

        _stop_training_run(state)

        api.terminate.assert_called_once()
        trainer.terminate.assert_not_called()


class TestStopTrainingRunStatus:
    """Verify status transitions in _stop_training_run."""

    def test_sets_status_to_stopped_when_running(self):
        state = _make_run_state(status="running")
        _stop_training_run(state)
        assert state.status == "stopped"

    def test_does_not_change_status_when_failed(self):
        state = _make_run_state(status="failed")
        _stop_training_run(state)
        assert state.status == "failed"

    def test_does_not_change_status_when_pending(self):
        state = _make_run_state(status="pending")
        _stop_training_run(state)
        assert state.status == "pending"

    def test_no_crash_with_no_processes_and_no_files(self):
        state = _make_run_state()
        _stop_training_run(state)  # should not raise
        assert state.status == "pending"


class TestSpawnTrainingRunEnvSanitization:
    @pytest.mark.asyncio
    async def test_trainer_env_preserves_tinker_and_wandb_only(self, tmp_path, monkeypatch):
        captured_envs = []

        class FakeProcess:
            def __init__(self):
                self.returncode = 0

            def poll(self):
                return None

            def terminate(self):
                return None

        def fake_popen(cmd, **kwargs):
            captured_envs.append(kwargs.get("env"))
            return FakeProcess()

        monkeypatch.setattr("tools.rl_training_tool.LOGS_DIR", tmp_path / "logs")
        monkeypatch.setattr(
            "tools.rl_training_tool._environments",
            [SimpleNamespace(name="test_env", file_path=tmp_path / "env.py")],
        )
        monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak")
        monkeypatch.setenv("TINKER_API_KEY", "tinker-key")
        monkeypatch.setenv("WANDB_API_KEY", "wandb-key")

        state = _make_run_state(environment="test_env")
        config_path = tmp_path / "config.yaml"
        config_path.write_text("run: test\n")

        def fake_create_task(coro):
            coro.close()
            return MagicMock()

        with (
            patch("tools.rl_training_tool.subprocess.Popen", side_effect=fake_popen),
            patch("tools.rl_training_tool.asyncio.sleep", new=AsyncMock()),
            patch("tools.rl_training_tool.asyncio.create_task", side_effect=fake_create_task),
        ):
            await _spawn_training_run(state, config_path)

        trainer_env = captured_envs[1]
        assert trainer_env["TINKER_API_KEY"] == "tinker-key"
        assert trainer_env["WANDB_API_KEY"] == "wandb-key"
        assert "OPENAI_API_KEY" not in trainer_env
