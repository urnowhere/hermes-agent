"""Tests for per-profile subprocess HOME isolation (#4426).

Verifies that subprocesses (terminal, execute_code, background processes)
receive a per-profile HOME directory while the Python process's own HOME
and Path.home() remain unchanged.

See: https://github.com/NousResearch/hermes-agent/issues/4426
"""

import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# get_subprocess_home()
# ---------------------------------------------------------------------------

class TestGetSubprocessHome:
    """Unit tests for hermes_constants.get_subprocess_home()."""

    def test_returns_none_when_hermes_home_unset(self, monkeypatch):
        monkeypatch.delenv("HERMES_HOME", raising=False)
        from hermes_constants import get_subprocess_home
        assert get_subprocess_home() is None

    def test_returns_none_when_home_dir_missing(self, tmp_path, monkeypatch):
        hermes_home = tmp_path / ".hermes"
        hermes_home.mkdir()
        monkeypatch.setenv("HERMES_HOME", str(hermes_home))
        # No home/ subdirectory created
        from hermes_constants import get_subprocess_home
        assert get_subprocess_home() is None

    def test_returns_path_when_home_dir_exists(self, tmp_path, monkeypatch):
        hermes_home = tmp_path / ".hermes"
        hermes_home.mkdir()
        profile_home = hermes_home / "home"
        profile_home.mkdir()
        monkeypatch.setenv("HERMES_HOME", str(hermes_home))
        from hermes_constants import get_subprocess_home
        assert get_subprocess_home() == str(profile_home)

    def test_returns_profile_specific_path(self, tmp_path, monkeypatch):
        """Named profiles get their own isolated HOME."""
        profile_dir = tmp_path / ".hermes" / "profiles" / "coder"
        profile_dir.mkdir(parents=True)
        profile_home = profile_dir / "home"
        profile_home.mkdir()
        monkeypatch.setenv("HERMES_HOME", str(profile_dir))
        from hermes_constants import get_subprocess_home
        assert get_subprocess_home() == str(profile_home)

    def test_two_profiles_get_different_homes(self, tmp_path, monkeypatch):
        base = tmp_path / ".hermes" / "profiles"
        for name in ("alpha", "beta"):
            p = base / name
            p.mkdir(parents=True)
            (p / "home").mkdir()

        from hermes_constants import get_subprocess_home

        monkeypatch.setenv("HERMES_HOME", str(base / "alpha"))
        home_a = get_subprocess_home()

        monkeypatch.setenv("HERMES_HOME", str(base / "beta"))
        home_b = get_subprocess_home()

        assert home_a != home_b
        assert home_a.endswith("alpha/home")
        assert home_b.endswith("beta/home")

    def test_honors_context_override_when_process_env_points_at_gateway_home(self, tmp_path, monkeypatch):
        """Topic-routed subprocess HOME must follow the active profile context."""
        gateway_home = tmp_path / "gateway"
        profile_dir = tmp_path / "profiles" / "cybrel-test"
        gateway_home.mkdir()
        profile_dir.mkdir(parents=True)
        (gateway_home / "home").mkdir()
        (profile_dir / "home").mkdir()
        monkeypatch.setenv("HERMES_HOME", str(gateway_home))

        from hermes_constants import get_subprocess_home, hermes_home_context

        with hermes_home_context(profile_dir):
            assert get_subprocess_home() == str(profile_dir / "home")


# ---------------------------------------------------------------------------
# _make_run_env() injection
# ---------------------------------------------------------------------------

class TestMakeRunEnvHomeInjection:
    """Verify _make_run_env() injects HOME into subprocess envs."""

    def test_injects_home_when_profile_home_exists(self, tmp_path, monkeypatch):
        hermes_home = tmp_path / "hermes"
        hermes_home.mkdir()
        (hermes_home / "home").mkdir()
        monkeypatch.setenv("HERMES_HOME", str(hermes_home))
        monkeypatch.setenv("HOME", "/root")
        monkeypatch.setenv("PATH", "/usr/bin:/bin")

        from tools.environments.local import _make_run_env
        result = _make_run_env({})

        assert result["HOME"] == str(hermes_home / "home")

    def test_no_injection_when_home_dir_missing(self, tmp_path, monkeypatch):
        hermes_home = tmp_path / "hermes"
        hermes_home.mkdir()
        # No home/ subdirectory
        monkeypatch.setenv("HERMES_HOME", str(hermes_home))
        monkeypatch.setenv("HOME", "/root")
        monkeypatch.setenv("PATH", "/usr/bin:/bin")

        from tools.environments.local import _make_run_env
        result = _make_run_env({})

        assert result["HOME"] == "/root"

    def test_no_injection_when_hermes_home_unset(self, monkeypatch):
        monkeypatch.delenv("HERMES_HOME", raising=False)
        monkeypatch.setenv("HOME", "/home/user")
        monkeypatch.setenv("PATH", "/usr/bin:/bin")

        from tools.environments.local import _make_run_env
        result = _make_run_env({})

        assert result["HOME"] == "/home/user"

    def test_injects_profile_hermes_home_when_context_override_is_active(self, tmp_path, monkeypatch):
        gateway_home = tmp_path / "gateway"
        profile_dir = tmp_path / "profiles" / "cybrel-test"
        gateway_home.mkdir()
        profile_dir.mkdir(parents=True)
        (gateway_home / "home").mkdir()
        (profile_dir / "home").mkdir()
        monkeypatch.setenv("HERMES_HOME", str(gateway_home))
        monkeypatch.setenv("HOME", "/root")
        monkeypatch.setenv("PATH", "/usr/bin:/bin")

        from hermes_constants import hermes_home_context
        from tools.environments.local import _make_run_env

        with hermes_home_context(profile_dir):
            result = _make_run_env({})

        assert result["HERMES_HOME"] == str(profile_dir)
        assert result["HOME"] == str(profile_dir / "home")


# ---------------------------------------------------------------------------
# _sanitize_subprocess_env() injection
# ---------------------------------------------------------------------------

class TestSanitizeSubprocessEnvHomeInjection:
    """Verify _sanitize_subprocess_env() injects HOME for background procs."""

    def test_injects_home_when_profile_home_exists(self, tmp_path, monkeypatch):
        hermes_home = tmp_path / "hermes"
        hermes_home.mkdir()
        (hermes_home / "home").mkdir()
        monkeypatch.setenv("HERMES_HOME", str(hermes_home))

        base_env = {"HOME": "/root", "PATH": "/usr/bin", "USER": "root"}
        from tools.environments.local import _sanitize_subprocess_env
        result = _sanitize_subprocess_env(base_env)

        assert result["HOME"] == str(hermes_home / "home")

    def test_no_injection_when_home_dir_missing(self, tmp_path, monkeypatch):
        hermes_home = tmp_path / "hermes"
        hermes_home.mkdir()
        monkeypatch.setenv("HERMES_HOME", str(hermes_home))

        base_env = {"HOME": "/root", "PATH": "/usr/bin"}
        from tools.environments.local import _sanitize_subprocess_env
        result = _sanitize_subprocess_env(base_env)

        assert result["HOME"] == "/root"

    def test_injects_profile_hermes_home_when_context_override_is_active(self, tmp_path, monkeypatch):
        gateway_home = tmp_path / "gateway"
        profile_dir = tmp_path / "profiles" / "cybrel-test"
        gateway_home.mkdir()
        profile_dir.mkdir(parents=True)
        (gateway_home / "home").mkdir()
        (profile_dir / "home").mkdir()
        monkeypatch.setenv("HERMES_HOME", str(gateway_home))

        base_env = {"HOME": "/root", "PATH": "/usr/bin"}
        from hermes_constants import hermes_home_context
        from tools.environments.local import _sanitize_subprocess_env

        with hermes_home_context(profile_dir):
            result = _sanitize_subprocess_env(base_env)

        assert result["HERMES_HOME"] == str(profile_dir)
        assert result["HOME"] == str(profile_dir / "home")


# ---------------------------------------------------------------------------
# Terminal/process runtime cache isolation
# ---------------------------------------------------------------------------

class TestTerminalRuntimeProfileIsolation:
    """Regression tests for profile-scoped terminal/process runtime caches."""

    def test_environment_cache_key_returns_tuple(self, tmp_path, monkeypatch):
        profile_home = tmp_path / "profiles" / "alpha"
        profile_home.mkdir(parents=True)
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "gateway"))

        from hermes_constants import hermes_home_context
        from tools.terminal_tool import _environment_cache_key

        with hermes_home_context(profile_home):
            key = _environment_cache_key("default")

        assert key == (str(profile_home), "default")

    def test_file_ops_cache_isolated_per_profile(self, tmp_path, monkeypatch):
        gateway_home = tmp_path / "gateway"
        profile_a = tmp_path / "profiles" / "alpha"
        profile_b = tmp_path / "profiles" / "beta"
        for home in (gateway_home, profile_a, profile_b):
            home.mkdir(parents=True)
        monkeypatch.setenv("HERMES_HOME", str(gateway_home))

        from hermes_constants import hermes_home_context
        from tools import file_tools
        from tools import terminal_tool

        terminal_tool._active_environments.clear()
        terminal_tool._last_activity.clear()
        terminal_tool._creation_locks.clear()
        file_tools._file_ops_cache.clear()

        created = []

        def fake_create_environment(**kwargs):
            env = SimpleNamespace(
                cwd=f"/workspace/{len(created)}",
                execute=lambda *args, **kw: {"output": "", "returncode": 0},
            )
            created.append((kwargs, env))
            return env

        monkeypatch.setattr(terminal_tool, "_create_environment", fake_create_environment)
        monkeypatch.setattr(terminal_tool, "_start_cleanup_thread", lambda: None)

        with hermes_home_context(profile_a):
            ops_a = file_tools._get_file_ops("default")
        with hermes_home_context(profile_b):
            ops_b = file_tools._get_file_ops("default")

        assert ops_a is not ops_b
        assert set(file_tools._file_ops_cache) == {
            (str(profile_a), "default"),
            (str(profile_b), "default"),
        }

    def test_execute_code_env_isolated_per_profile(self, tmp_path, monkeypatch):
        gateway_home = tmp_path / "gateway"
        profile_a = tmp_path / "profiles" / "alpha"
        profile_b = tmp_path / "profiles" / "beta"
        for home in (gateway_home, profile_a, profile_b):
            home.mkdir(parents=True)
        monkeypatch.setenv("HERMES_HOME", str(gateway_home))

        from hermes_constants import hermes_home_context
        from tools import code_execution_tool
        from tools import terminal_tool

        terminal_tool._active_environments.clear()
        terminal_tool._last_activity.clear()
        terminal_tool._creation_locks.clear()

        created = []

        def fake_create_environment(**kwargs):
            env = SimpleNamespace(cwd=f"/workspace/{len(created)}")
            created.append((kwargs, env))
            return env

        monkeypatch.setattr(terminal_tool, "_create_environment", fake_create_environment)
        monkeypatch.setattr(terminal_tool, "_start_cleanup_thread", lambda: None)

        with hermes_home_context(profile_a):
            env_a, _ = code_execution_tool._get_or_create_env("default")
        with hermes_home_context(profile_b):
            env_b, _ = code_execution_tool._get_or_create_env("default")

        assert env_a is not env_b
        assert set(terminal_tool._active_environments) == {
            (str(profile_a), "default"),
            (str(profile_b), "default"),
        }

    def test_legacy_string_cache_key_does_not_crash_eviction_loop(self, tmp_path):
        from tools import terminal_tool
        from tools.process_registry import process_registry

        cleaned = []
        legacy_key = f"{tmp_path / 'legacy'}::default"
        env = SimpleNamespace(cleanup=lambda: cleaned.append(True))
        terminal_tool._active_environments.clear()
        terminal_tool._last_activity.clear()
        terminal_tool._creation_locks.clear()
        process_registry._running.clear()
        terminal_tool._active_environments[legacy_key] = env
        terminal_tool._last_activity[legacy_key] = 0.0

        terminal_tool._cleanup_inactive_envs(lifetime_seconds=1)

        assert cleaned == [True]
        assert legacy_key not in terminal_tool._active_environments

    def test_process_registry_writes_global_checkpoint_with_profile_metadata(
        self, tmp_path, monkeypatch
    ):
        gateway_home = tmp_path / "gateway"
        profile_a = tmp_path / "profiles" / "alpha"
        profile_b = tmp_path / "profiles" / "beta"
        gateway_home.mkdir()
        profile_a.mkdir(parents=True)
        profile_b.mkdir(parents=True)
        monkeypatch.setenv("HERMES_HOME", str(gateway_home))

        from hermes_constants import hermes_home_context
        from tools import process_registry as process_registry_mod
        from tools.process_registry import ProcessRegistry, ProcessSession

        checkpoint = gateway_home / "processes.json"
        monkeypatch.setattr(process_registry_mod, "CHECKPOINT_PATH", checkpoint)
        registry = ProcessRegistry()
        with hermes_home_context(profile_a):
            registry._running["proc_alpha"] = ProcessSession(
                id="proc_alpha",
                command="sleep 60",
                task_id="default",
                session_key="agent:alpha:telegram:group:-1001:101",
                agent_profile="alpha",
                agent_hermes_home=str(profile_a),
                pid=12345,
                started_at=1.0,
            )
        with hermes_home_context(profile_b):
            registry._running["proc_beta"] = ProcessSession(
                id="proc_beta",
                command="sleep 60",
                task_id="default",
                session_key="agent:beta:telegram:group:-1001:202",
                agent_profile="beta",
                agent_hermes_home=str(profile_b),
                pid=23456,
                started_at=2.0,
            )
            registry._write_checkpoint()

        rows = json.loads(checkpoint.read_text(encoding="utf-8"))
        by_id = {row["session_id"]: row for row in rows}
        assert set(by_id) == {"proc_alpha", "proc_beta"}
        assert by_id["proc_alpha"]["agent_hermes_home"] == str(profile_a)
        assert by_id["proc_beta"]["agent_hermes_home"] == str(profile_b)

    def test_process_registry_recovers_routed_processes_after_crash(
        self, tmp_path, monkeypatch
    ):
        gateway_home = tmp_path / "gateway"
        profile_a = tmp_path / "profiles" / "alpha"
        profile_b = tmp_path / "profiles" / "beta"
        gateway_home.mkdir()
        profile_a.mkdir(parents=True)
        profile_b.mkdir(parents=True)
        monkeypatch.setenv("HERMES_HOME", str(gateway_home))

        from tools import process_registry as process_registry_mod
        from tools.process_registry import ProcessRegistry, ProcessSession

        checkpoint = gateway_home / "processes.json"
        monkeypatch.setattr(process_registry_mod, "CHECKPOINT_PATH", checkpoint)
        before = ProcessRegistry()
        before._running["proc_alpha"] = ProcessSession(
            id="proc_alpha",
            command="sleep 60",
            task_id="default",
            session_key="agent:alpha:telegram:group:-1001:101",
            agent_profile="alpha",
            agent_hermes_home=str(profile_a),
            pid=12345,
            started_at=1.0,
            watcher_platform="telegram",
            watcher_chat_id="-1001",
            watcher_thread_id="101",
            watcher_interval=5,
            notify_on_complete=True,
        )
        before._running["proc_beta"] = ProcessSession(
            id="proc_beta",
            command="sleep 60",
            task_id="default",
            session_key="agent:beta:telegram:group:-1001:202",
            agent_profile="beta",
            agent_hermes_home=str(profile_b),
            pid=23456,
            started_at=2.0,
            watcher_platform="telegram",
            watcher_chat_id="-1001",
            watcher_thread_id="202",
            watcher_interval=5,
            notify_on_complete=True,
        )
        before._write_checkpoint()

        after = ProcessRegistry()
        monkeypatch.setattr(after, "_is_host_pid_alive", lambda _pid: True)

        assert after.recover_from_checkpoint() == 2
        assert set(after._running) == {"proc_alpha", "proc_beta"}
        assert after._running["proc_alpha"].agent_hermes_home == str(profile_a)
        assert after._running["proc_beta"].agent_hermes_home == str(profile_b)
        assert {w["agent_hermes_home"] for w in after.pending_watchers} == {
            str(profile_a),
            str(profile_b),
        }

    def test_session_context_propagates_routed_profile_to_terminal_tool(
        self, tmp_path, monkeypatch
    ):
        gateway_home = tmp_path / "gateway"
        profile_home = tmp_path / "profiles" / "alpha"
        for home in (gateway_home, profile_home):
            (home / "home").mkdir(parents=True)
        monkeypatch.setenv("HERMES_HOME", str(gateway_home))
        monkeypatch.setenv("TERMINAL_ENV", "local")
        monkeypatch.setenv("TERMINAL_LOCAL_PERSISTENT", "false")

        from gateway.session_context import clear_session_vars, set_session_vars
        from tools.process_registry import process_registry
        from tools.terminal_tool import cleanup_all_environments, terminal_tool

        process_registry._running.clear()
        process_registry._finished.clear()
        tokens = set_session_vars(
            platform="telegram",
            chat_id="-1001",
            thread_id="101",
            session_key="agent:alpha:telegram:group:-1001:101",
            agent_profile="alpha",
            agent_hermes_home=str(profile_home),
        )
        try:
            started = json.loads(
                terminal_tool("printf ok", background=True, notify_on_complete=True, timeout=5)
            )
            session = process_registry.get(started["session_id"])
        finally:
            clear_session_vars(tokens)
            cleanup_all_environments()

        assert session is not None
        assert session.agent_profile == "alpha"
        assert session.agent_hermes_home == str(profile_home)

    def test_process_action_rejects_cross_profile_session_id(
        self, tmp_path
    ):
        profile_a = tmp_path / "profiles" / "alpha"
        profile_b = tmp_path / "profiles" / "beta"
        profile_a.mkdir(parents=True)
        profile_b.mkdir(parents=True)

        from gateway.session_context import clear_session_vars, set_session_vars
        from tools.process_registry import ProcessSession, _handle_process, process_registry

        try:
            process_registry._running.clear()
            process_registry._finished.clear()
            process_registry._running["proc_alpha"] = ProcessSession(
                id="proc_alpha",
                command="sleep 60",
                task_id="default",
                session_key="agent:alpha:telegram:group:-1001:101",
                agent_profile="alpha",
                agent_hermes_home=str(profile_a),
                pid=12345,
                started_at=1.0,
            )

            tokens = set_session_vars(agent_profile="beta", agent_hermes_home=str(profile_b))
            try:
                for action in ("poll", "log", "wait", "kill", "write", "submit", "close"):
                    result = json.loads(
                        _handle_process(
                            {
                                "action": action,
                                "session_id": "proc_alpha",
                                "data": "x",
                                "timeout": 0,
                            }
                        )
                    )
                    assert result["status"] == "not_found"
            finally:
                clear_session_vars(tokens)
        finally:
            process_registry._running.clear()
            process_registry._finished.clear()

    def test_routed_profile_rejects_legacy_process_without_profile_home(
        self, tmp_path
    ):
        profile_home = tmp_path / "profiles" / "beta"
        profile_home.mkdir(parents=True)

        from gateway.session_context import clear_session_vars, set_session_vars
        from tools.process_registry import ProcessSession, _handle_process, process_registry

        try:
            process_registry._running.clear()
            process_registry._finished.clear()
            process_registry._running["proc_legacy"] = ProcessSession(
                id="proc_legacy",
                command="sleep 60",
                task_id="default",
                session_key="agent:main:telegram:group:-1001:101",
                pid=12345,
                started_at=1.0,
            )

            tokens = set_session_vars(agent_profile="beta", agent_hermes_home=str(profile_home))
            try:
                result = json.loads(
                    _handle_process({"action": "log", "session_id": "proc_legacy"})
                )
                assert result["status"] == "not_found"
            finally:
                clear_session_vars(tokens)
        finally:
            process_registry._running.clear()
            process_registry._finished.clear()

    def test_main_context_rejects_routed_process_session_id(
        self, tmp_path
    ):
        profile_home = tmp_path / "profiles" / "alpha"
        profile_home.mkdir(parents=True)

        from gateway.session_context import clear_session_vars, set_session_vars
        from tools.process_registry import ProcessSession, _handle_process, process_registry

        try:
            process_registry._running.clear()
            process_registry._finished.clear()
            process_registry._running["proc_alpha"] = ProcessSession(
                id="proc_alpha",
                command="sleep 60",
                task_id="default",
                session_key="agent:alpha:telegram:group:-1001:101",
                agent_profile="alpha",
                agent_hermes_home=str(profile_home),
                pid=12345,
                started_at=1.0,
            )

            tokens = set_session_vars(agent_profile="", agent_hermes_home="")
            try:
                result = json.loads(
                    _handle_process({"action": "log", "session_id": "proc_alpha"})
                )
                assert result["status"] == "not_found"
            finally:
                clear_session_vars(tokens)
        finally:
            process_registry._running.clear()
            process_registry._finished.clear()

    def test_process_list_filters_to_current_profile_scope(
        self, tmp_path
    ):
        profile_a = tmp_path / "profiles" / "alpha"
        profile_b = tmp_path / "profiles" / "beta"
        profile_a.mkdir(parents=True)
        profile_b.mkdir(parents=True)

        from gateway.session_context import clear_session_vars, set_session_vars
        from tools.process_registry import ProcessSession, _handle_process, process_registry

        try:
            process_registry._running.clear()
            process_registry._finished.clear()
            process_registry._running["proc_main"] = ProcessSession(
                id="proc_main",
                command="sleep 60",
                task_id="default",
                session_key="agent:main:telegram:group:-1001:1",
                pid=111,
                started_at=1.0,
            )
            process_registry._running["proc_alpha"] = ProcessSession(
                id="proc_alpha",
                command="sleep 60",
                task_id="default",
                session_key="agent:alpha:telegram:group:-1001:101",
                agent_profile="alpha",
                agent_hermes_home=str(profile_a),
                pid=222,
                started_at=2.0,
            )
            process_registry._running["proc_beta"] = ProcessSession(
                id="proc_beta",
                command="sleep 60",
                task_id="default",
                session_key="agent:beta:telegram:group:-1001:202",
                agent_profile="beta",
                agent_hermes_home=str(profile_b),
                pid=333,
                started_at=3.0,
            )

            tokens = set_session_vars(agent_profile="beta", agent_hermes_home=str(profile_b))
            try:
                routed = json.loads(_handle_process({"action": "list"}))
                assert [p["session_id"] for p in routed["processes"]] == ["proc_beta"]
            finally:
                clear_session_vars(tokens)

            main = json.loads(_handle_process({"action": "list"}))
            assert [p["session_id"] for p in main["processes"]] == ["proc_main"]
        finally:
            process_registry._running.clear()
            process_registry._finished.clear()


# ---------------------------------------------------------------------------
# Profile bootstrap
# ---------------------------------------------------------------------------

class TestProfileBootstrap:
    """Verify new profiles get a home/ subdirectory."""

    def test_profile_dirs_includes_home(self):
        from hermes_cli.profiles import _PROFILE_DIRS
        assert "home" in _PROFILE_DIRS

    def test_create_profile_bootstraps_home_dir(self, tmp_path, monkeypatch):
        """create_profile() should create home/ inside the profile dir."""
        home = tmp_path / ".hermes"
        home.mkdir()
        monkeypatch.setattr(Path, "home", lambda: tmp_path)
        monkeypatch.setenv("HERMES_HOME", str(home))

        from hermes_cli.profiles import create_profile
        profile_dir = create_profile("testbot", no_alias=True)
        assert (profile_dir / "home").is_dir()


# ---------------------------------------------------------------------------
# Python process HOME unchanged
# ---------------------------------------------------------------------------

class TestPythonProcessUnchanged:
    """Confirm the Python process's own HOME is never modified."""

    def test_path_home_unchanged_after_subprocess_home_resolved(
        self, tmp_path, monkeypatch
    ):
        hermes_home = tmp_path / "hermes"
        hermes_home.mkdir()
        (hermes_home / "home").mkdir()
        monkeypatch.setenv("HERMES_HOME", str(hermes_home))

        original_home = os.environ.get("HOME")
        original_path_home = str(Path.home())

        from hermes_constants import get_subprocess_home
        sub_home = get_subprocess_home()

        # Subprocess home is set but Python HOME stays the same
        assert sub_home is not None
        assert os.environ.get("HOME") == original_home
        assert str(Path.home()) == original_path_home
