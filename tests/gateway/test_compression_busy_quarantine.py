from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from gateway.platforms.base import MessageEvent
from tests.gateway.restart_test_helpers import make_restart_runner, make_restart_source


class CompressingAgent:
    def __init__(self):
        self.interrupt = MagicMock()

    def get_activity_summary(self):
        return {
            "compression_in_progress": True,
            "api_call_count": 4,
            "max_iterations": 200,
            "current_tool": None,
        }


@pytest.mark.asyncio
async def test_busy_message_queues_instead_of_interrupting_during_compression():
    runner, adapter = make_restart_runner()
    source = make_restart_source()
    session_key = runner._session_key_for_source(source)
    agent = CompressingAgent()
    runner._running_agents[session_key] = agent
    runner._running_agents_ts[session_key] = 0
    runner._busy_input_mode = "interrupt"
    runner._busy_ack_ts = {}

    event = MessageEvent(text="follow up", source=source, message_id="m1")

    handled = await runner._handle_active_session_busy_message(event, session_key)

    assert handled is True
    agent.interrupt.assert_not_called()
    queued = adapter._pending_messages[session_key]
    assert queued.text == "follow up"
    assert adapter.sent
    assert "Compression in progress" in adapter.sent[-1]
    assert "queued for next turn" in adapter.sent[-1]


def test_agent_activity_summary_exposes_compression_state():
    from run_agent import AIAgent

    agent = object.__new__(AIAgent)
    agent._last_activity_ts = 100.0
    agent._last_activity_desc = "testing"
    agent._current_tool = None
    agent._api_call_count = 2
    agent.max_iterations = 200
    agent.iteration_budget = SimpleNamespace(used=2, max_total=200)
    agent._compression_in_progress = True
    agent._compression_started_at = 99.0

    summary = agent.get_activity_summary()

    assert summary["compression_in_progress"] is True
    assert summary["compression_elapsed"] is not None
