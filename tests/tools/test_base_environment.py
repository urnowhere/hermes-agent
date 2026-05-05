"""Tests for BaseEnvironment unified execution model.

Tests _wrap_command(), _extract_cwd_from_output(), _embed_stdin_heredoc(),
init_session() failure handling, and the CWD marker contract.
"""

import uuid
from unittest.mock import MagicMock

from tools.environments.base import BaseEnvironment, _cwd_marker


class _TestableEnv(BaseEnvironment):
    """Concrete subclass for testing base class methods."""

    def __init__(self, cwd="/tmp", timeout=10):
        super().__init__(cwd=cwd, timeout=timeout)

    def _run_bash(self, cmd_string, *, login=False, timeout=120, stdin_data=None):
        raise NotImplementedError("Use mock")

    def cleanup(self):
        pass


class TestWrapCommand:
    def test_basic_shape(self):
        env = _TestableEnv()
        env._snapshot_ready = True
        wrapped = env._wrap_command("echo hello", "/tmp")

        assert "source" in wrapped
        assert "cd -- /tmp" in wrapped or "cd -- '/tmp'" in wrapped
        assert "eval 'echo hello'" in wrapped
        assert "__hermes_ec=$?" in wrapped
        assert "export -p >" in wrapped
        assert "pwd -P >" in wrapped
        assert env._cwd_marker in wrapped
        assert "exit $__hermes_ec" in wrapped

    def test_no_snapshot_skips_source(self):
        env = _TestableEnv()
        env._snapshot_ready = False
        wrapped = env._wrap_command("echo hello", "/tmp")

        assert "source" not in wrapped

    def test_single_quote_escaping(self):
        env = _TestableEnv()
        env._snapshot_ready = True
        wrapped = env._wrap_command("echo 'hello world'", "/tmp")

        assert "eval 'echo '\\''hello world'\\'''" in wrapped

    def test_tilde_not_quoted(self):
        env = _TestableEnv()
        env._snapshot_ready = True
        wrapped = env._wrap_command("ls", "~")

        assert "cd -- ~" in wrapped
        assert "cd -- '~'" not in wrapped

    def test_tilde_subpath_with_spaces_uses_home_and_quotes_suffix(self):
        env = _TestableEnv()
        env._snapshot_ready = True
        wrapped = env._wrap_command("ls", "~/my repo")

        assert "cd -- $HOME/'my repo'" in wrapped
        assert "cd -- ~/my repo" not in wrapped

    def test_tilde_slash_maps_to_home(self):
        env = _TestableEnv()
        env._snapshot_ready = True
        wrapped = env._wrap_command("ls", "~/")

        assert "cd -- $HOME" in wrapped
        assert "cd -- ~/" not in wrapped

    def test_hyphen_prefixed_workdir_is_passed_after_double_dash(self):
        env = _TestableEnv()
        env._snapshot_ready = True
        wrapped = env._wrap_command("pwd", "-demo")

        assert "builtin cd -- -demo || exit 126" in wrapped

    def test_cd_failure_exit_126(self):
        env = _TestableEnv()
        env._snapshot_ready = True
        wrapped = env._wrap_command("ls", "/nonexistent")

        assert "exit 126" in wrapped


class TestWrapCommandTimezone:
    """_wrap_command must inject `export TZ=<name>` so every backend that
    flows through BaseEnvironment.execute (local, docker, ssh, singularity,
    modal, daytona) gets the user's configured timezone inside the
    subprocess shell."""

    def test_injects_tz_export_when_configured(self, monkeypatch):
        from zoneinfo import ZoneInfo
        import hermes_time
        monkeypatch.setattr(hermes_time, "get_timezone", lambda: ZoneInfo("Asia/Shanghai"))
        env = _TestableEnv()
        env._snapshot_ready = True
        wrapped = env._wrap_command("date", "/tmp")
        assert "export TZ=Asia/Shanghai" in wrapped

    def test_skips_tz_export_when_unset(self, monkeypatch):
        import hermes_time
        monkeypatch.setattr(hermes_time, "get_timezone", lambda: None)
        env = _TestableEnv()
        env._snapshot_ready = True
        wrapped = env._wrap_command("date", "/tmp")
        assert "export TZ" not in wrapped

    def test_tz_export_after_snapshot_source(self, monkeypatch):
        """Configured TZ must override anything sourced from the snapshot."""
        from zoneinfo import ZoneInfo
        import hermes_time
        monkeypatch.setattr(hermes_time, "get_timezone", lambda: ZoneInfo("Asia/Shanghai"))
        env = _TestableEnv()
        env._snapshot_ready = True
        wrapped = env._wrap_command("date", "/tmp")
        source_idx = wrapped.find("source ")
        export_idx = wrapped.find("export TZ=")
        assert source_idx != -1 and export_idx != -1
        assert source_idx < export_idx, "TZ export must come after snapshot source"

    def test_tz_export_present_without_snapshot(self, monkeypatch):
        """Even when snapshot is not ready, TZ should still be exported."""
        from zoneinfo import ZoneInfo
        import hermes_time
        monkeypatch.setattr(hermes_time, "get_timezone", lambda: ZoneInfo("Asia/Shanghai"))
        env = _TestableEnv()
        env._snapshot_ready = False
        wrapped = env._wrap_command("date", "/tmp")
        assert "source" not in wrapped
        assert "export TZ=Asia/Shanghai" in wrapped


class TestResolveConfiguredTzName:
    def test_returns_iana_key_when_configured(self, monkeypatch):
        from zoneinfo import ZoneInfo
        import hermes_time
        from tools.environments.base import _resolve_configured_tz_name
        monkeypatch.setattr(hermes_time, "get_timezone", lambda: ZoneInfo("Asia/Kolkata"))
        assert _resolve_configured_tz_name() == "Asia/Kolkata"

    def test_returns_none_when_unset(self, monkeypatch):
        import hermes_time
        from tools.environments.base import _resolve_configured_tz_name
        monkeypatch.setattr(hermes_time, "get_timezone", lambda: None)
        assert _resolve_configured_tz_name() is None

    def test_returns_none_on_resolver_error(self, monkeypatch):
        """If hermes_time.get_timezone raises, the helper must swallow it."""
        import hermes_time
        from tools.environments.base import _resolve_configured_tz_name
        def _boom():
            raise RuntimeError("resolver failed")
        monkeypatch.setattr(hermes_time, "get_timezone", _boom)
        assert _resolve_configured_tz_name() is None


class TestExtractCwdFromOutput:
    def test_happy_path(self):
        env = _TestableEnv()
        marker = env._cwd_marker
        result = {
            "output": f"hello\n{marker}/home/user{marker}\n",
        }
        env._extract_cwd_from_output(result)

        assert env.cwd == "/home/user"
        assert marker not in result["output"]

    def test_missing_marker(self):
        env = _TestableEnv()
        result = {"output": "hello world\n"}
        env._extract_cwd_from_output(result)

        assert env.cwd == "/tmp"  # unchanged

    def test_marker_in_command_output(self):
        """If the marker appears in command output AND as the real marker,
        rfind grabs the last (real) one."""
        env = _TestableEnv()
        marker = env._cwd_marker
        result = {
            "output": f"user typed {marker} in their output\nreal output\n{marker}/correct/path{marker}\n",
        }
        env._extract_cwd_from_output(result)

        assert env.cwd == "/correct/path"

    def test_output_cleaned(self):
        env = _TestableEnv()
        marker = env._cwd_marker
        result = {
            "output": f"hello\n{marker}/tmp{marker}\n",
        }
        env._extract_cwd_from_output(result)

        assert "hello" in result["output"]
        assert marker not in result["output"]


class TestEmbedStdinHeredoc:
    def test_heredoc_format(self):
        result = BaseEnvironment._embed_stdin_heredoc("cat", "hello world")

        assert result.startswith("cat << '")
        assert "hello world" in result
        assert "HERMES_STDIN_" in result

    def test_unique_delimiter_each_call(self):
        r1 = BaseEnvironment._embed_stdin_heredoc("cat", "data")
        r2 = BaseEnvironment._embed_stdin_heredoc("cat", "data")

        # Extract delimiters
        d1 = r1.split("'")[1]
        d2 = r2.split("'")[1]
        assert d1 != d2  # UUID-based, should be unique


class TestInitSessionFailure:
    def test_snapshot_ready_false_on_failure(self):
        env = _TestableEnv()

        def failing_run_bash(*args, **kwargs):
            raise RuntimeError("bash not found")

        env._run_bash = failing_run_bash
        env.init_session()

        assert env._snapshot_ready is False

    def test_login_flag_when_snapshot_not_ready(self):
        """When _snapshot_ready=False, execute() should pass login=True to _run_bash."""
        env = _TestableEnv()
        env._snapshot_ready = False

        calls = []
        def mock_run_bash(cmd, *, login=False, timeout=120, stdin_data=None):
            calls.append({"login": login})
            # Return a mock process handle
            mock = MagicMock()
            mock.poll.return_value = 0
            mock.returncode = 0
            mock.stdout = iter([])
            return mock

        env._run_bash = mock_run_bash
        env.execute("echo test")

        assert len(calls) == 1
        assert calls[0]["login"] is True


class TestCwdMarker:
    def test_marker_contains_session_id(self):
        env = _TestableEnv()
        assert env._session_id in env._cwd_marker

    def test_unique_per_instance(self):
        env1 = _TestableEnv()
        env2 = _TestableEnv()
        assert env1._cwd_marker != env2._cwd_marker
