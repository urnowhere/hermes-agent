"""Tests for TZ injection in BaseModalExecutionEnvironment._prepare_modal_exec.

Managed Modal does not flow through BaseEnvironment.execute / _wrap_command,
so its TZ propagation is wired in `_prepare_modal_exec` instead. This file
covers that path.
"""

from typing import Any

from tools.environments.modal_utils import (
    BaseModalExecutionEnvironment,
    ModalExecStart,
    PreparedModalExec,
)


class _TestableModalEnv(BaseModalExecutionEnvironment):
    """Minimal concrete subclass for testing _prepare_modal_exec in isolation."""

    def __init__(self):
        super().__init__(cwd="/root", timeout=60)

    def _start_modal_exec(self, prepared: PreparedModalExec) -> ModalExecStart:
        raise NotImplementedError("test harness")

    def _poll_modal_exec(self, handle: Any) -> dict | None:
        raise NotImplementedError("test harness")

    def _cancel_modal_exec(self, handle: Any) -> None:
        raise NotImplementedError("test harness")

    def cleanup(self):
        pass


class TestPrepareModalExecTimezone:
    def test_injects_tz_export_when_configured(self, monkeypatch):
        from zoneinfo import ZoneInfo
        import hermes_time
        monkeypatch.setattr(hermes_time, "get_timezone", lambda: ZoneInfo("Asia/Shanghai"))
        env = _TestableModalEnv()
        prepared = env._prepare_modal_exec("date")
        assert prepared.command.startswith("export TZ=Asia/Shanghai; ")
        assert prepared.command.endswith("date")

    def test_skips_tz_when_unset(self, monkeypatch):
        import hermes_time
        monkeypatch.setattr(hermes_time, "get_timezone", lambda: None)
        env = _TestableModalEnv()
        prepared = env._prepare_modal_exec("date")
        assert "export TZ" not in prepared.command
        assert prepared.command == "date"

    def test_tz_injected_after_sudo_wrap(self, monkeypatch):
        """If a sudo wrap rewrites the command, TZ export still wraps the
        whole thing so the elevated process inherits it."""
        from zoneinfo import ZoneInfo
        import hermes_time
        monkeypatch.setattr(hermes_time, "get_timezone", lambda: ZoneInfo("Asia/Shanghai"))
        env = _TestableModalEnv()
        prepared = env._prepare_modal_exec("echo hello")
        # No sudo here, but verify ordering: TZ comes first
        assert prepared.command.index("export TZ=") < prepared.command.index("echo hello")

    def test_tz_propagates_with_stdin_heredoc(self, monkeypatch):
        """Stdin-via-heredoc transports must still get TZ in front of the wrap."""
        from zoneinfo import ZoneInfo
        import hermes_time
        monkeypatch.setattr(hermes_time, "get_timezone", lambda: ZoneInfo("Asia/Shanghai"))
        env = _TestableModalEnv()
        env._stdin_mode = "heredoc"
        prepared = env._prepare_modal_exec("cat", stdin_data="hello\n")
        assert prepared.command.startswith("export TZ=Asia/Shanghai; ")
        assert "<<" in prepared.command  # heredoc marker still present
