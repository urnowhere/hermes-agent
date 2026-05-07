import pytest

from tests.gateway.restart_test_helpers import make_restart_runner


@pytest.mark.asyncio
async def test_shutdown_notification_skips_when_no_active_gateway_task():
    runner, adapter = make_restart_runner()

    await runner._notify_active_sessions_of_shutdown()

    assert adapter.sent == []


@pytest.mark.asyncio
async def test_shutdown_notification_dedupes_same_active_session_within_window():
    runner, adapter = make_restart_runner()
    session_key = "agent:main:telegram:dm:12345"
    runner._running_agents[session_key] = object()
    runner._restart_requested = True

    await runner._notify_active_sessions_of_shutdown()
    await runner._notify_active_sessions_of_shutdown()

    assert len(adapter.sent) == 1
    assert "Gateway restarting" in adapter.sent[0]
