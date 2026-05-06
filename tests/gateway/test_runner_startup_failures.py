import asyncio
import logging
from datetime import datetime, timedelta, timezone

import pytest
from unittest.mock import AsyncMock

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import BasePlatformAdapter
from gateway.run import GatewayRunner
from gateway.status import read_runtime_status
from tools.teams_pipeline_store import TeamsPipelineStore


class _RetryableFailureAdapter(BasePlatformAdapter):
    def __init__(self):
        super().__init__(PlatformConfig(enabled=True, token="***"), Platform.TELEGRAM)

    async def connect(self) -> bool:
        self._set_fatal_error(
            "telegram_connect_error",
            "Telegram startup failed: temporary DNS resolution failure.",
            retryable=True,
        )
        return False

    async def disconnect(self) -> None:
        self._mark_disconnected()

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        raise NotImplementedError

    async def get_chat_info(self, chat_id):
        return {"id": chat_id}


class _DisabledAdapter(BasePlatformAdapter):
    def __init__(self):
        super().__init__(PlatformConfig(enabled=False, token="***"), Platform.TELEGRAM)

    async def connect(self) -> bool:
        raise AssertionError("connect should not be called for disabled platforms")

    async def disconnect(self) -> None:
        self._mark_disconnected()

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        raise NotImplementedError

    async def get_chat_info(self, chat_id):
        return {"id": chat_id}


class _SuccessfulAdapter(BasePlatformAdapter):
    def __init__(self):
        super().__init__(PlatformConfig(enabled=True, token="***"), Platform.DISCORD)

    async def connect(self) -> bool:
        return True

    async def disconnect(self) -> None:
        self._mark_disconnected()

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        raise NotImplementedError

    async def get_chat_info(self, chat_id):
        return {"id": chat_id}


class _SuccessfulMSGraphAdapter(BasePlatformAdapter):
    def __init__(self):
        super().__init__(PlatformConfig(enabled=True), Platform.MSGRAPH_WEBHOOK)

    async def connect(self) -> bool:
        self._mark_connected()
        return True

    async def disconnect(self) -> None:
        self._mark_disconnected()

    async def send(self, chat_id, content, reply_to=None, metadata=None):
        raise NotImplementedError

    async def get_chat_info(self, chat_id):
        return {"id": chat_id}


@pytest.mark.anyio
async def test_runner_returns_failure_for_retryable_startup_errors(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    config = GatewayConfig(
        platforms={
            Platform.TELEGRAM: PlatformConfig(enabled=True, token="***")
        },
        sessions_dir=tmp_path / "sessions",
    )
    runner = GatewayRunner(config)

    monkeypatch.setattr(runner, "_create_adapter", lambda platform, platform_config: _RetryableFailureAdapter())

    ok = await runner.start()

    assert ok is False
    assert runner.should_exit_cleanly is False
    state = read_runtime_status()
    assert state["gateway_state"] == "startup_failed"
    assert "temporary DNS resolution failure" in state["exit_reason"]
    assert state["platforms"]["telegram"]["state"] == "retrying"
    assert state["platforms"]["telegram"]["error_code"] == "telegram_connect_error"


@pytest.mark.anyio
async def test_runner_allows_cron_only_mode_when_no_platforms_are_enabled(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    config = GatewayConfig(
        platforms={
            Platform.TELEGRAM: PlatformConfig(enabled=False, token="***")
        },
        sessions_dir=tmp_path / "sessions",
    )
    runner = GatewayRunner(config)

    ok = await runner.start()

    assert ok is True
    assert runner.should_exit_cleanly is False
    assert runner.adapters == {}
    state = read_runtime_status()
    assert state["gateway_state"] == "running"


@pytest.mark.anyio
async def test_runner_records_connected_platform_state_on_success(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    config = GatewayConfig(
        platforms={
            Platform.DISCORD: PlatformConfig(enabled=True, token="***")
        },
        sessions_dir=tmp_path / "sessions",
    )
    runner = GatewayRunner(config)

    monkeypatch.setattr(runner, "_create_adapter", lambda platform, platform_config: _SuccessfulAdapter())
    monkeypatch.setattr(runner.hooks, "discover_and_load", lambda: None)
    monkeypatch.setattr(runner.hooks, "emit", AsyncMock())

    ok = await runner.start()

    assert ok is True
    state = read_runtime_status()
    assert state["gateway_state"] == "running"
    assert state["platforms"]["discord"]["state"] == "connected"
    assert state["platforms"]["discord"]["error_code"] is None
    assert state["platforms"]["discord"]["error_message"] is None


@pytest.mark.anyio
async def test_runner_fails_fast_when_msgraph_webhook_missing_graph_credentials(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    for key in ("MSGRAPH_TENANT_ID", "MSGRAPH_CLIENT_ID", "MSGRAPH_CLIENT_SECRET"):
        monkeypatch.delenv(key, raising=False)

    config = GatewayConfig(
        platforms={
            Platform.MSGRAPH_WEBHOOK: PlatformConfig(enabled=True, extra={})
        },
        sessions_dir=tmp_path / "sessions",
    )
    runner = GatewayRunner(config)

    ok = await runner.start()

    assert ok is True
    assert runner.should_exit_cleanly is True
    state = read_runtime_status()
    assert state["gateway_state"] == "startup_failed"
    assert "missing MSGRAPH_TENANT_ID" in state["exit_reason"]
    assert state["platforms"]["msgraph_webhook"]["state"] == "fatal"
    assert state["platforms"]["msgraph_webhook"]["error_code"] == "config_validation_failed"


@pytest.mark.anyio
async def test_runner_fails_fast_when_teams_incoming_webhook_missing_url(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("TEAMS_INCOMING_WEBHOOK_URL", raising=False)
    monkeypatch.setenv("TEAMS_DELIVERY_MODE", "incoming_webhook")

    config = GatewayConfig(
        platforms={
            Platform.TEAMS: PlatformConfig(
                enabled=True,
                extra={"delivery_mode": "incoming_webhook"},
            )
        },
        sessions_dir=tmp_path / "sessions",
    )
    runner = GatewayRunner(config)

    ok = await runner.start()

    assert ok is True
    assert runner.should_exit_cleanly is True
    state = read_runtime_status()
    assert state["gateway_state"] == "startup_failed"
    assert "TEAMS_INCOMING_WEBHOOK_URL" in state["exit_reason"]
    assert state["platforms"]["teams"]["state"] == "fatal"
    assert state["platforms"]["teams"]["error_code"] == "config_validation_failed"


@pytest.mark.anyio
async def test_runner_warns_when_msgraph_subscription_store_is_empty(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("MSGRAPH_TENANT_ID", "tenant")
    monkeypatch.setenv("MSGRAPH_CLIENT_ID", "client")
    monkeypatch.setenv("MSGRAPH_CLIENT_SECRET", "secret")

    store_path = tmp_path / "teams_pipeline_store.json"
    config = GatewayConfig(
        platforms={
            Platform.MSGRAPH_WEBHOOK: PlatformConfig(
                enabled=True,
                extra={"store_path": str(store_path)},
            )
        },
        sessions_dir=tmp_path / "sessions",
    )
    runner = GatewayRunner(config)
    monkeypatch.setattr(runner, "_create_adapter", lambda platform, platform_config: _SuccessfulMSGraphAdapter())
    monkeypatch.setattr(runner.hooks, "discover_and_load", lambda: None)
    monkeypatch.setattr(runner.hooks, "emit", AsyncMock())

    with caplog.at_level(logging.WARNING):
        ok = await runner.start()

    assert ok is True
    assert "no stored Graph subscriptions were found" in caplog.text


@pytest.mark.anyio
async def test_runner_warns_when_msgraph_subscriptions_are_expiring(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("MSGRAPH_TENANT_ID", "tenant")
    monkeypatch.setenv("MSGRAPH_CLIENT_ID", "client")
    monkeypatch.setenv("MSGRAPH_CLIENT_SECRET", "secret")

    store_path = tmp_path / "teams_pipeline_store.json"
    store = TeamsPipelineStore(store_path)
    expires_at = (datetime.now(timezone.utc) + timedelta(hours=1)).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    store.upsert_subscription(
        "sub-1",
        {
            "subscription_id": "sub-1",
            "resource": "communications/onlineMeetings/getAllTranscripts",
            "change_type": "updated",
            "notification_url": "https://example.com/webhooks/msgraph",
            "expiration_datetime": expires_at,
        },
    )

    config = GatewayConfig(
        platforms={
            Platform.MSGRAPH_WEBHOOK: PlatformConfig(
                enabled=True,
                extra={"store_path": str(store_path)},
            )
        },
        sessions_dir=tmp_path / "sessions",
    )
    runner = GatewayRunner(config)
    monkeypatch.setattr(runner, "_create_adapter", lambda platform, platform_config: _SuccessfulMSGraphAdapter())
    monkeypatch.setattr(runner.hooks, "discover_and_load", lambda: None)
    monkeypatch.setattr(runner.hooks, "emit", AsyncMock())

    with caplog.at_level(logging.WARNING):
        ok = await runner.start()

    assert ok is True
    assert "expiring within 24 hours" in caplog.text


@pytest.mark.anyio
async def test_runner_runs_msgraph_subscription_maintenance_once(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    config = GatewayConfig(
        platforms={
            Platform.MSGRAPH_WEBHOOK: PlatformConfig(
                enabled=True,
                extra={
                    "store_path": str(tmp_path / "teams_pipeline_store.json"),
                    "renew_within_hours": 12,
                    "extend_hours": 36,
                    "maintenance_interval_seconds": 60,
                    "maintenance_initial_delay_seconds": 0,
                },
            )
        },
        sessions_dir=tmp_path / "sessions",
    )
    runner = GatewayRunner(config)
    seen = {}

    async def fake_maintain(settings):
        seen.update(settings)
        return {
            "remote_subscription_count": 1,
            "synced_subscription_count": 1,
            "candidate_count": 1,
            "renewed_count": 1,
            "dry_run": False,
        }

    monkeypatch.setattr(
        "tools.microsoft_graph_subscription_tools.maintain_graph_subscriptions",
        fake_maintain,
    )
    runner.adapters[Platform.MSGRAPH_WEBHOOK] = _SuccessfulMSGraphAdapter()
    runner.adapters[Platform.MSGRAPH_WEBHOOK]._mark_connected()

    await runner._run_msgraph_subscription_maintenance_once()

    assert seen["renew_within_hours"] == 12
    assert seen["extend_hours"] == 36
    assert seen["store_path"].endswith("teams_pipeline_store.json")


@pytest.mark.anyio
async def test_runner_skips_msgraph_subscription_maintenance_when_adapter_not_connected(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    config = GatewayConfig(
        platforms={
            Platform.MSGRAPH_WEBHOOK: PlatformConfig(
                enabled=True,
                extra={"store_path": str(tmp_path / "teams_pipeline_store.json")},
            )
        },
        sessions_dir=tmp_path / "sessions",
    )
    runner = GatewayRunner(config)
    called = {"value": False}

    async def fake_maintain(settings):
        called["value"] = True
        return {}

    monkeypatch.setattr(
        "tools.microsoft_graph_subscription_tools.maintain_graph_subscriptions",
        fake_maintain,
    )

    await runner._run_msgraph_subscription_maintenance_once()

    assert called["value"] is False


@pytest.mark.anyio
async def test_runner_start_schedules_msgraph_subscription_maintenance_watcher(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("MSGRAPH_TENANT_ID", "tenant")
    monkeypatch.setenv("MSGRAPH_CLIENT_ID", "client")
    monkeypatch.setenv("MSGRAPH_CLIENT_SECRET", "secret")

    store_path = tmp_path / "teams_pipeline_store.json"
    config = GatewayConfig(
        platforms={
            Platform.MSGRAPH_WEBHOOK: PlatformConfig(
                enabled=True,
                extra={
                    "store_path": str(store_path),
                    "maintenance_initial_delay_seconds": 0,
                    "maintenance_interval_seconds": 60,
                },
            )
        },
        sessions_dir=tmp_path / "sessions",
    )
    runner = GatewayRunner(config)
    monkeypatch.setattr(runner, "_create_adapter", lambda platform, platform_config: _SuccessfulMSGraphAdapter())
    monkeypatch.setattr(runner.hooks, "discover_and_load", lambda: None)
    monkeypatch.setattr(runner.hooks, "emit", AsyncMock())
    called = {"value": False}

    async def fake_watcher():
        called["value"] = True

    monkeypatch.setattr(runner, "_msgraph_subscription_maintenance_watcher", fake_watcher)

    ok = await runner.start()
    await asyncio.sleep(0)

    assert ok is True
    assert called["value"] is True
    await runner.stop()


@pytest.mark.anyio
async def test_start_gateway_verbosity_imports_redacting_formatter(monkeypatch, tmp_path):
    """Verbosity != None must not crash with NameError on RedactingFormatter (#8044)."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    class _CleanExitRunner:
        def __init__(self, config):
            self.config = config
            self.should_exit_cleanly = True
            self.exit_reason = None
            self.adapters = {}

        async def start(self):
            return True

        async def stop(self):
            return None

    monkeypatch.setattr("gateway.status.get_running_pid", lambda: None)
    monkeypatch.setattr("tools.skills_sync.sync_skills", lambda quiet=True: None)
    monkeypatch.setattr("hermes_logging.setup_logging", lambda hermes_home, mode: tmp_path)
    monkeypatch.setattr("hermes_logging._add_rotating_handler", lambda *args, **kwargs: None)
    monkeypatch.setattr("gateway.run.GatewayRunner", _CleanExitRunner)

    from gateway.run import start_gateway

    # verbosity=1 triggers the code path that uses RedactingFormatter.
    # Before the fix this raised NameError.
    ok = await start_gateway(config=GatewayConfig(), replace=False, verbosity=1)

    assert ok is True


@pytest.mark.anyio
async def test_start_gateway_replace_force_uses_terminate_pid(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    calls = []

    class _CleanExitRunner:
        def __init__(self, config):
            self.config = config
            self.should_exit_cleanly = True
            self.exit_reason = None
            self.adapters = {}

        async def start(self):
            return True

        async def stop(self):
            return None

    # get_running_pid returns 42 before we kill the old gateway, then None
    # after remove_pid_file() clears the record (reflects real behavior).
    _pid_state = {"alive": True}
    def _mock_get_running_pid():
        return 42 if _pid_state["alive"] else None
    def _mock_remove_pid_file():
        _pid_state["alive"] = False
    monkeypatch.setattr("gateway.status.get_running_pid", _mock_get_running_pid)
    monkeypatch.setattr("gateway.status.remove_pid_file", _mock_remove_pid_file)
    monkeypatch.setattr(
        "gateway.status.release_all_scoped_locks",
        lambda **kwargs: 0,
    )
    monkeypatch.setattr("gateway.status.terminate_pid", lambda pid, force=False: calls.append((pid, force)))
    monkeypatch.setattr("gateway.run.os.getpid", lambda: 100)
    monkeypatch.setattr("gateway.run.os.kill", lambda pid, sig: None)
    monkeypatch.setattr("time.sleep", lambda _: None)
    monkeypatch.setattr("tools.skills_sync.sync_skills", lambda quiet=True: None)
    monkeypatch.setattr("hermes_logging.setup_logging", lambda hermes_home, mode: tmp_path)
    monkeypatch.setattr("hermes_logging._add_rotating_handler", lambda *args, **kwargs: None)
    monkeypatch.setattr("gateway.run.GatewayRunner", _CleanExitRunner)

    from gateway.run import start_gateway

    ok = await start_gateway(config=GatewayConfig(), replace=True, verbosity=None)

    assert ok is True
    assert calls == [(42, False), (42, True)]


@pytest.mark.anyio
async def test_start_gateway_replace_writes_takeover_marker_before_sigterm(
    monkeypatch, tmp_path
):
    """--replace must write a takeover marker BEFORE sending SIGTERM.

    The marker lets the target's shutdown handler identify the signal as a
    planned takeover (→ exit 0) rather than an unexpected kill (→ exit 1).
    Without the marker, PR #5646's signal-recovery path would revive the
    target via systemd Restart=on-failure, starting a flap loop.
    """
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    # Record the ORDER of marker-write + terminate_pid calls
    events: list[str] = []
    marker_paths_seen: list = []

    def record_write_marker(target_pid: int) -> bool:
        events.append(f"write_marker(target_pid={target_pid})")
        # Also check that the marker file actually exists after this call
        marker_paths_seen.append(
            (tmp_path / ".gateway-takeover.json").exists() is False  # not yet
        )
        # Actually write the marker so we can verify cleanup later
        from gateway.status import _get_takeover_marker_path, _write_json_file, _get_process_start_time
        _write_json_file(_get_takeover_marker_path(), {
            "target_pid": target_pid,
            "target_start_time": 0,
            "replacer_pid": 100,
            "written_at": "2026-04-17T00:00:00+00:00",
        })
        return True

    def record_terminate(pid, force=False):
        events.append(f"terminate_pid(pid={pid}, force={force})")

    class _CleanExitRunner:
        def __init__(self, config):
            self.config = config
            self.should_exit_cleanly = True
            self.exit_reason = None
            self.adapters = {}

        async def start(self):
            return True

        async def stop(self):
            return None

    _pid_state = {"alive": True}
    def _mock_get_running_pid():
        return 42 if _pid_state["alive"] else None
    def _mock_remove_pid_file():
        _pid_state["alive"] = False
    monkeypatch.setattr("gateway.status.get_running_pid", _mock_get_running_pid)
    monkeypatch.setattr("gateway.status.remove_pid_file", _mock_remove_pid_file)
    monkeypatch.setattr(
        "gateway.status.release_all_scoped_locks",
        lambda **kwargs: 0,
    )
    monkeypatch.setattr("gateway.status.write_takeover_marker", record_write_marker)
    monkeypatch.setattr("gateway.status.terminate_pid", record_terminate)
    monkeypatch.setattr("gateway.run.os.getpid", lambda: 100)
    # Simulate old process exiting on first check so we don't loop into force-kill
    monkeypatch.setattr(
        "gateway.run.os.kill",
        lambda pid, sig: (_ for _ in ()).throw(ProcessLookupError()),
    )
    monkeypatch.setattr("time.sleep", lambda _: None)
    monkeypatch.setattr("tools.skills_sync.sync_skills", lambda quiet=True: None)
    monkeypatch.setattr("hermes_logging.setup_logging", lambda hermes_home, mode: tmp_path)
    monkeypatch.setattr("hermes_logging._add_rotating_handler", lambda *args, **kwargs: None)
    monkeypatch.setattr("gateway.run.GatewayRunner", _CleanExitRunner)

    from gateway.run import start_gateway

    ok = await start_gateway(config=GatewayConfig(), replace=True, verbosity=None)

    assert ok is True
    # Ordering: marker written BEFORE SIGTERM
    assert events[0] == "write_marker(target_pid=42)"
    assert any(e.startswith("terminate_pid(pid=42") for e in events[1:])
    # Marker file cleanup: replacer cleans it after loop completes
    assert not (tmp_path / ".gateway-takeover.json").exists()


@pytest.mark.anyio
async def test_start_gateway_replace_clears_marker_on_permission_denied(
    monkeypatch, tmp_path
):
    """If we fail to kill the existing PID (permission denied), clean up the
    marker so it doesn't grief an unrelated future shutdown."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    def write_marker(target_pid: int) -> bool:
        from gateway.status import _get_takeover_marker_path, _write_json_file
        _write_json_file(_get_takeover_marker_path(), {
            "target_pid": target_pid,
            "target_start_time": 0,
            "replacer_pid": 100,
            "written_at": "2026-04-17T00:00:00+00:00",
        })
        return True

    def raise_permission(pid, force=False):
        raise PermissionError("simulated EPERM")

    monkeypatch.setattr("gateway.status.get_running_pid", lambda: 42)
    monkeypatch.setattr("gateway.status.write_takeover_marker", write_marker)
    monkeypatch.setattr("gateway.status.terminate_pid", raise_permission)
    monkeypatch.setattr("gateway.run.os.getpid", lambda: 100)
    monkeypatch.setattr("tools.skills_sync.sync_skills", lambda quiet=True: None)
    monkeypatch.setattr("hermes_logging.setup_logging", lambda hermes_home, mode: tmp_path)
    monkeypatch.setattr("hermes_logging._add_rotating_handler", lambda *args, **kwargs: None)

    from gateway.run import start_gateway

    # Should return False due to permission error
    ok = await start_gateway(config=GatewayConfig(), replace=True, verbosity=None)

    assert ok is False
    # Marker must NOT be left behind
    assert not (tmp_path / ".gateway-takeover.json").exists()


def test_runner_warns_when_docker_gateway_lacks_explicit_output_mount(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("TERMINAL_ENV", "docker")
    monkeypatch.setenv("TERMINAL_DOCKER_VOLUMES", '["/etc/localtime:/etc/localtime:ro"]')
    config = GatewayConfig(
        platforms={
            Platform.TELEGRAM: PlatformConfig(enabled=True, token="***")
        },
        sessions_dir=tmp_path / "sessions",
    )

    with caplog.at_level("WARNING"):
        GatewayRunner(config)

    assert any(
        "host-visible output mount" in record.message
        for record in caplog.records
    )
