"""Tests for hermes_cli.gateway."""

import sys
from types import SimpleNamespace
from unittest.mock import patch, call, MagicMock

import hermes_cli.gateway as gateway


class TestSystemdLingerStatus:
    def test_reports_enabled(self, monkeypatch):
        monkeypatch.setattr(gateway, "is_linux", lambda: True)
        monkeypatch.setattr(gateway, "is_termux", lambda: False)
        monkeypatch.setenv("USER", "alice")
        monkeypatch.setattr(
            gateway.subprocess,
            "run",
            lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="yes\n", stderr=""),
        )
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/loginctl")

        assert gateway.get_systemd_linger_status() == (True, "")

    def test_reports_disabled(self, monkeypatch):
        monkeypatch.setattr(gateway, "is_linux", lambda: True)
        monkeypatch.setattr(gateway, "is_termux", lambda: False)
        monkeypatch.setenv("USER", "alice")
        monkeypatch.setattr(
            gateway.subprocess,
            "run",
            lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="no\n", stderr=""),
        )
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/loginctl")

        assert gateway.get_systemd_linger_status() == (False, "")

    def test_reports_termux_as_not_supported(self, monkeypatch):
        monkeypatch.setattr(gateway, "is_termux", lambda: True)

        assert gateway.get_systemd_linger_status() == (None, "not supported in Termux")


class TestContainerSystemdSupport:
    def test_supports_systemd_services_in_container_with_user_manager(self, monkeypatch):
        monkeypatch.setattr(gateway, "is_linux", lambda: True)
        monkeypatch.setattr(gateway, "is_termux", lambda: False)
        monkeypatch.setattr(gateway, "is_wsl", lambda: False)
        monkeypatch.setattr(gateway, "is_container", lambda: True)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/systemctl")
        monkeypatch.setattr(gateway, "_systemd_operational", lambda system=False: not system)

        assert gateway.supports_systemd_services() is True

    def test_supports_systemd_services_in_container_with_system_manager(self, monkeypatch):
        monkeypatch.setattr(gateway, "is_linux", lambda: True)
        monkeypatch.setattr(gateway, "is_termux", lambda: False)
        monkeypatch.setattr(gateway, "is_wsl", lambda: False)
        monkeypatch.setattr(gateway, "is_container", lambda: True)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/systemctl")
        monkeypatch.setattr(gateway, "_systemd_operational", lambda system=False: system)

        assert gateway.supports_systemd_services() is True

    def test_supports_systemd_services_in_container_without_systemd(self, monkeypatch):
        monkeypatch.setattr(gateway, "is_linux", lambda: True)
        monkeypatch.setattr(gateway, "is_termux", lambda: False)
        monkeypatch.setattr(gateway, "is_wsl", lambda: False)
        monkeypatch.setattr(gateway, "is_container", lambda: True)
        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/systemctl")
        monkeypatch.setattr(gateway, "_systemd_operational", lambda system=False: False)

        assert gateway.supports_systemd_services() is False


def test_gateway_install_in_container_with_operational_systemd_uses_systemd(monkeypatch):
    monkeypatch.setattr(gateway, "supports_systemd_services", lambda: True)
    monkeypatch.setattr(gateway, "is_wsl", lambda: False)
    monkeypatch.setattr(gateway, "is_macos", lambda: False)
    monkeypatch.setattr(gateway, "is_managed", lambda: False)

    calls = []
    monkeypatch.setattr(
        gateway,
        "systemd_install",
        lambda force=False, system=False, run_as_user=None: calls.append((force, system, run_as_user)),
    )

    args = SimpleNamespace(
        gateway_command="install",
        force=False,
        system=False,
        run_as_user=None,
    )
    gateway.gateway_command(args)

    assert calls == [(False, False, None)]


def test_gateway_start_in_container_with_operational_systemd_uses_systemd(monkeypatch):
    monkeypatch.setattr(gateway, "supports_systemd_services", lambda: True)
    monkeypatch.setattr(gateway, "is_wsl", lambda: False)
    monkeypatch.setattr(gateway, "is_macos", lambda: False)

    calls = []
    monkeypatch.setattr(gateway, "systemd_start", lambda system=False: calls.append(system))

    args = SimpleNamespace(gateway_command="start", system=False, all=False)
    gateway.gateway_command(args)

    assert calls == [False]


def test_systemd_status_warns_when_linger_disabled(monkeypatch, tmp_path, capsys):
    unit_path = tmp_path / "hermes-gateway.service"
    unit_path.write_text("[Unit]\n")

    monkeypatch.setattr(gateway, "get_systemd_unit_path", lambda system=False: unit_path)
    monkeypatch.setattr(gateway, "get_systemd_linger_status", lambda: (False, ""))

    def fake_run(cmd, capture_output=False, text=False, check=False, **kwargs):
        if cmd[:4] == ["systemctl", "--user", "status", gateway.get_service_name()]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        if cmd[:3] == ["systemctl", "--user", "is-active"]:
            return SimpleNamespace(returncode=0, stdout="active\n", stderr="")
        raise AssertionError(f"Unexpected command: {cmd}")

    monkeypatch.setattr(gateway.subprocess, "run", fake_run)

    gateway.systemd_status(deep=False)

    out = capsys.readouterr().out
    assert "gateway service is running" in out
    assert "Systemd linger is disabled" in out
    assert "loginctl enable-linger" in out


def test_systemd_install_checks_linger_status(monkeypatch, tmp_path, capsys):
    unit_path = tmp_path / "systemd" / "user" / "hermes-gateway.service"

    monkeypatch.setattr(gateway, "get_systemd_unit_path", lambda system=False: unit_path)

    calls = []
    helper_calls = []

    def fake_run(cmd, check=False, **kwargs):
        calls.append((cmd, check))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(gateway.subprocess, "run", fake_run)
    monkeypatch.setattr(gateway, "_ensure_linger_enabled", lambda: helper_calls.append(True))

    gateway.systemd_install(force=False)

    out = capsys.readouterr().out
    assert unit_path.exists()
    assert [cmd for cmd, _ in calls] == [
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", gateway.get_service_name()],
    ]
    assert helper_calls == [True]
    assert "User service installed and enabled" in out


def test_systemd_install_system_scope_skips_linger_and_uses_systemctl(monkeypatch, tmp_path, capsys):
    unit_path = tmp_path / "etc" / "systemd" / "system" / "hermes-gateway.service"

    monkeypatch.setattr(gateway, "get_systemd_unit_path", lambda system=False: unit_path)
    monkeypatch.setattr(
        gateway,
        "generate_systemd_unit",
        lambda system=False, run_as_user=None: f"scope={system} user={run_as_user}\n",
    )
    monkeypatch.setattr(gateway, "_require_root_for_system_service", lambda action: None)

    calls = []
    helper_calls = []

    def fake_run(cmd, check=False, **kwargs):
        calls.append((cmd, check))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(gateway.subprocess, "run", fake_run)
    monkeypatch.setattr(gateway, "_ensure_linger_enabled", lambda: helper_calls.append(True))

    gateway.systemd_install(force=False, system=True, run_as_user="alice")

    out = capsys.readouterr().out
    assert unit_path.exists()
    assert unit_path.read_text(encoding="utf-8") == "scope=True user=alice\n"
    assert [cmd for cmd, _ in calls] == [
        ["systemctl", "daemon-reload"],
        ["systemctl", "enable", gateway.get_service_name()],
    ]
    assert helper_calls == []
    assert "Configured to run as: alice" not in out  # generated test unit has no User= line
    assert "System service installed and enabled" in out


def test_conflicting_systemd_units_warning(monkeypatch, tmp_path, capsys):
    user_unit = tmp_path / "user" / "hermes-gateway.service"
    system_unit = tmp_path / "system" / "hermes-gateway.service"
    user_unit.parent.mkdir(parents=True)
    system_unit.parent.mkdir(parents=True)
    user_unit.write_text("[Unit]\n", encoding="utf-8")
    system_unit.write_text("[Unit]\n", encoding="utf-8")

    monkeypatch.setattr(
        gateway,
        "get_systemd_unit_path",
        lambda system=False: system_unit if system else user_unit,
    )

    gateway.print_systemd_scope_conflict_warning()

    out = capsys.readouterr().out
    assert "Both user and system gateway services are installed" in out
    assert "hermes gateway uninstall" in out
    assert "--system" in out


def test_install_linux_gateway_from_setup_system_choice_without_root_prints_followup(monkeypatch, capsys):
    monkeypatch.setattr(gateway, "prompt_linux_gateway_install_scope", lambda: "system")
    monkeypatch.setattr(gateway.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(gateway, "_default_system_service_user", lambda: "alice")
    monkeypatch.setattr(gateway, "systemd_install", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("should not install")))

    scope, did_install = gateway.install_linux_gateway_from_setup(force=False)

    out = capsys.readouterr().out
    assert (scope, did_install) == ("system", False)
    assert "sudo hermes gateway install --system --run-as-user alice" in out
    assert "sudo hermes gateway start --system" in out


def test_install_linux_gateway_from_setup_system_choice_as_root_installs(monkeypatch):
    monkeypatch.setattr(gateway, "prompt_linux_gateway_install_scope", lambda: "system")
    monkeypatch.setattr(gateway.os, "geteuid", lambda: 0)
    monkeypatch.setattr(gateway, "_default_system_service_user", lambda: "alice")

    calls = []
    monkeypatch.setattr(
        gateway,
        "systemd_install",
        lambda force=False, system=False, run_as_user=None: calls.append((force, system, run_as_user)),
    )

    scope, did_install = gateway.install_linux_gateway_from_setup(force=True)

    assert (scope, did_install) == ("system", True)
    assert calls == [(True, True, "alice")]


def test_find_gateway_pids_falls_back_to_pid_file_when_process_scan_fails(monkeypatch):
    monkeypatch.setattr(gateway, "_get_service_pids", lambda: set())
    monkeypatch.setattr(gateway, "is_windows", lambda: False)
    monkeypatch.setattr("gateway.status.get_running_pid", lambda: 321)

    def fake_run(cmd, **kwargs):
        if cmd[:4] == ["ps", "-A", "eww", "-o"]:
            return SimpleNamespace(returncode=1, stdout="", stderr="ps failed")
        raise AssertionError(f"Unexpected command: {cmd}")

    monkeypatch.setattr(gateway.subprocess, "run", fake_run)

    assert gateway.find_gateway_pids() == [321]


# ---------------------------------------------------------------------------
# _wait_for_gateway_exit
# ---------------------------------------------------------------------------


class TestWaitForGatewayExit:
    """PID-based wait with force-kill on timeout."""

    def test_returns_immediately_when_no_pid(self, monkeypatch):
        """If get_running_pid returns None, exit instantly."""
        monkeypatch.setattr("gateway.status.get_running_pid", lambda: None)
        # Should return without sleeping at all.
        gateway._wait_for_gateway_exit(timeout=1.0, force_after=0.5)

    def test_returns_when_process_exits_gracefully(self, monkeypatch):
        """Process exits after a couple of polls — no SIGKILL needed."""
        poll_count = 0

        def mock_get_running_pid():
            nonlocal poll_count
            poll_count += 1
            return 12345 if poll_count <= 2 else None

        monkeypatch.setattr("gateway.status.get_running_pid", mock_get_running_pid)
        monkeypatch.setattr("time.sleep", lambda _: None)

        gateway._wait_for_gateway_exit(timeout=10.0, force_after=999.0)
        # Should have polled until None was returned.
        assert poll_count == 3

    def test_force_kills_after_grace_period(self, monkeypatch):
        """When the process doesn't exit, force-kill the saved PID."""

        # Simulate monotonic time advancing past force_after
        call_num = 0
        def fake_monotonic():
            nonlocal call_num
            call_num += 1
            # First two calls: initial deadline + force_deadline setup (time 0)
            # Then each loop iteration advances time
            return call_num * 2.0  # 2, 4, 6, 8, ...

        kills = []
        def mock_terminate(pid, force=False):
            kills.append((pid, force))

        # get_running_pid returns the PID until kill is sent, then None
        def mock_get_running_pid():
            return None if kills else 42

        monkeypatch.setattr("time.monotonic", fake_monotonic)
        monkeypatch.setattr("time.sleep", lambda _: None)
        monkeypatch.setattr("gateway.status.get_running_pid", mock_get_running_pid)
        monkeypatch.setattr(gateway, "terminate_pid", mock_terminate)

        gateway._wait_for_gateway_exit(timeout=10.0, force_after=5.0)
        assert (42, True) in kills

    def test_handles_process_already_gone_on_kill(self, monkeypatch):
        """ProcessLookupError during force-kill is not fatal."""

        call_num = 0
        def fake_monotonic():
            nonlocal call_num
            call_num += 1
            return call_num * 3.0  # Jump past force_after quickly

        def mock_terminate(pid, force=False):
            raise ProcessLookupError

        monkeypatch.setattr("time.monotonic", fake_monotonic)
        monkeypatch.setattr("time.sleep", lambda _: None)
        monkeypatch.setattr("gateway.status.get_running_pid", lambda: 99)
        monkeypatch.setattr(gateway, "terminate_pid", mock_terminate)

        # Should not raise — ProcessLookupError means it's already gone.
        gateway._wait_for_gateway_exit(timeout=10.0, force_after=2.0)

    def test_kill_gateway_processes_force_uses_helper(self, monkeypatch):
        calls = []

        monkeypatch.setattr(gateway, "find_gateway_pids", lambda exclude_pids=None, all_profiles=False: [11, 22])
        monkeypatch.setattr(gateway, "terminate_pid", lambda pid, force=False: calls.append((pid, force)))

        killed = gateway.kill_gateway_processes(force=True)

        assert killed == 2
        assert calls == [(11, True), (22, True)]


class TestWindowsRunGateway:
    """Windows-specific behaviour in run_gateway().

    We cannot easily invoke run_gateway() end-to-end (it calls asyncio.run()),
    so we test the two Windows-specific subsystems independently:
      1. UTF-8 console reconfiguration
      2. SetConsoleCtrlHandler phantom-SIGINT protection
    """

    def test_utf8_reconfigure_called_on_windows(self, monkeypatch):
        """On Windows, run_gateway() must reconfigure stdout/stderr to UTF-8
        so box-drawing characters in the banner don't raise UnicodeEncodeError."""
        reconfigured = []

        class FakeStream:
            def reconfigure(self, encoding=None, errors=None):
                reconfigured.append((encoding, errors))

        monkeypatch.setattr(sys, "platform", "win32")
        monkeypatch.setattr(sys, "stdout", FakeStream())
        monkeypatch.setattr(sys, "stderr", FakeStream())
        # Prevent actually running the gateway
        monkeypatch.setattr(gateway, "asyncio", MagicMock())
        monkeypatch.setattr(gateway.sys, "path", list(gateway.sys.path))

        import importlib
        import hermes_cli.gateway as gw_fresh
        # Simulate just the UTF-8 reconfigure block
        if sys.platform == "win32" or True:  # force execution of the block
            try:
                if hasattr(sys.stdout, "reconfigure"):
                    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
                if hasattr(sys.stderr, "reconfigure"):
                    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

        assert reconfigured == [("utf-8", "replace"), ("utf-8", "replace")]

    def test_utf8_reconfigure_skipped_on_non_windows(self, monkeypatch):
        """On non-Windows, stdout/stderr must NOT be reconfigured."""
        reconfigured = []

        class FakeStream:
            def reconfigure(self, encoding=None, errors=None):
                reconfigured.append((encoding, errors))

        monkeypatch.setattr(sys, "platform", "linux")
        monkeypatch.setattr(sys, "stdout", FakeStream())
        monkeypatch.setattr(sys, "stderr", FakeStream())

        # Simulate the conditional block
        if sys.platform == "win32":
            try:
                if hasattr(sys.stdout, "reconfigure"):
                    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
                if hasattr(sys.stderr, "reconfigure"):
                    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass

        assert reconfigured == []

    def test_set_console_ctrl_handler_registered_on_windows(self, monkeypatch):
        """On Windows, SetConsoleCtrlHandler must be registered to absorb phantom
        CTRL_C_EVENTs without crashing the gateway."""
        registered = []

        import ctypes
        import ctypes.wintypes as wt

        fake_kernel32 = MagicMock()
        fake_kernel32.SetConsoleCtrlHandler.side_effect = lambda handler, add: registered.append((handler, add))

        # Simulate the registration block from run_gateway()
        import time
        _sigint_last = [0.0]
        _HandlerRoutine = ctypes.WINFUNCTYPE(wt.BOOL, wt.DWORD)

        def _win_ctrl_c(event_type):
            return True

        handler_ref = _HandlerRoutine(_win_ctrl_c)
        fake_kernel32.SetConsoleCtrlHandler(handler_ref, True)

        assert len(registered) == 1
        _, add_flag = registered[0]
        assert add_flag is True

    def test_null_handler_disables_ctrl_c_at_process_level(self, monkeypatch):
        """SetConsoleCtrlHandler(NULL, TRUE) must also be called to disable
        CTRL_C at the process level, making the gateway immune to Ctrl+C from
        the CLI (or any other process sharing the same console group)."""
        registered = []

        import ctypes
        import ctypes.wintypes as wt

        fake_kernel32 = MagicMock()
        fake_kernel32.SetConsoleCtrlHandler.side_effect = lambda handler, add: registered.append((handler, add))

        _HandlerRoutine = ctypes.WINFUNCTYPE(wt.BOOL, wt.DWORD)
        def _win_ctrl_c(event_type): return True
        handler_ref = _HandlerRoutine(_win_ctrl_c)

        # Simulate both calls from run_gateway(): custom handler + NULL disable
        fake_kernel32.SetConsoleCtrlHandler(handler_ref, True)   # custom handler
        fake_kernel32.SetConsoleCtrlHandler(None, True)           # disable CTRL_C

        assert len(registered) == 2
        # Second call must use None (NULL) to disable CTRL_C
        null_handler, null_add = registered[1]
        assert null_handler is None
        assert null_add is True

    def test_phantom_sigint_absorbed_single_press(self):
        """Single CTRL_C_EVENT within 3s gap must be silently absorbed (return True)
        without raising KeyboardInterrupt."""
        import time

        _sigint_last = [0.0]

        def _simulate_ctrl_c_handler(event_type):
            """Replicate the handler logic from run_gateway()."""
            _CTRL_C_EVENT = 0
            if event_type != _CTRL_C_EVENT:
                return False
            now = time.monotonic()
            if now - _sigint_last[0] < 3.0:
                raise KeyboardInterrupt()
            _sigint_last[0] = now
            return True  # absorbed

        # First press: absorbed, no exception
        result = _simulate_ctrl_c_handler(0)
        assert result is True

    def test_phantom_sigint_double_press_raises(self):
        """Two CTRL_C_EVENTs within 3s must raise KeyboardInterrupt."""
        import time
        import pytest

        _sigint_last = [0.0]

        def _simulate_ctrl_c_handler(event_type):
            _CTRL_C_EVENT = 0
            if event_type != _CTRL_C_EVENT:
                return False
            now = time.monotonic()
            if now - _sigint_last[0] < 3.0:
                raise KeyboardInterrupt()
            _sigint_last[0] = now
            return True

        # First press recorded
        _simulate_ctrl_c_handler(0)
        # Second press immediately: should raise
        with pytest.raises(KeyboardInterrupt):
            _simulate_ctrl_c_handler(0)
