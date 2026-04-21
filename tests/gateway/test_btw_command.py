"""Tests for /btw gateway slash command."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.config import Platform
from gateway.platforms.base import MessageEvent
from gateway.session import SessionSource


def _make_event(text="/btw", platform=Platform.TELEGRAM, user_id="12345", chat_id="67890"):
    source = SessionSource(
        platform=platform,
        user_id=user_id,
        chat_id=chat_id,
        user_name="testuser",
    )
    return MessageEvent(text=text, source=source)


def _make_runner():
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner.adapters = {}
    runner._voice_mode = {}
    runner._session_db = None
    runner._reasoning_config = None
    runner._provider_routing = {}
    runner._fallback_model = None
    runner._running_agents = {}
    runner._background_tasks = set()
    runner._active_btw_tasks = {}

    mock_store = MagicMock()
    mock_store.get_or_create_session.return_value = MagicMock(session_id="sess_123")
    runner.session_store = mock_store

    from gateway.hooks import HookRegistry
    runner.hooks = HookRegistry()

    return runner


class TestRunBtwTask:
    @pytest.mark.asyncio
    async def test_idle_transcript_filters_session_meta_before_agent_call(self):
        runner = _make_runner()
        runner.session_store.load_transcript.return_value = [
            {"role": "session_meta", "tools": [], "timestamp": "t0"},
            {"role": "user", "content": "main context", "timestamp": "t1"},
            {"role": "assistant", "content": "current answer", "timestamp": "t2"},
        ]

        mock_adapter = AsyncMock()
        mock_adapter.send = AsyncMock()
        mock_adapter.extract_media = MagicMock(return_value=([], "Looks good"))
        mock_adapter.extract_images = MagicMock(return_value=([], "Looks good"))
        runner.adapters[Platform.TELEGRAM] = mock_adapter

        source = _make_event(text="/btw what owns titles?").source
        fake_loop = MagicMock()
        fake_loop.run_in_executor = AsyncMock(side_effect=lambda pool, fn: fn())
        captured = {}

        class FakeAgent:
            def __init__(self, *args, **kwargs):
                pass

            def run_conversation(self, *, user_message, conversation_history, task_id, sync_honcho):
                captured["roles"] = [msg.get("role") for msg in conversation_history]
                return {"final_response": "Looks good", "messages": []}

        with patch("gateway.run._resolve_runtime_agent_kwargs", return_value={"api_key": "test-key"}), \
             patch("gateway.run._load_gateway_config", return_value={}), \
             patch("gateway.run._resolve_gateway_model", return_value="anthropic/claude-opus-4.6"), \
             patch("gateway.run.asyncio.get_event_loop", return_value=fake_loop), \
             patch("gateway.run.AIAgent", FakeAgent, create=True), \
             patch("run_agent.AIAgent", FakeAgent):
            await runner._run_btw_task("what owns titles?", source, "telegram:12345:67890", "btw_test")

        assert captured["roles"] == ["user", "assistant"]
        sent = mock_adapter.send.call_args.kwargs["content"]
        assert "💬 /btw" in sent
        assert "Looks good" in sent


class TestHandleMessageBtwDuringActiveRun:
    @pytest.mark.asyncio
    async def test_btw_does_not_interrupt_running_agent(self):
        runner = _make_runner()
        event = _make_event(text="/btw what owns titles?")
        session_key = "telegram:12345:67890"

        running_agent = MagicMock()
        runner._running_agents[session_key] = running_agent
        runner._pending_messages = {}
        runner._is_user_authorized = MagicMock(return_value=True)
        runner._session_key_for_source = MagicMock(return_value=session_key)
        runner._handle_btw_command = AsyncMock(return_value='💬 /btw: "what owns titles?"\nReply will appear here shortly.')

        result = await runner._handle_message(event)

        assert result == '💬 /btw: "what owns titles?"\nReply will appear here shortly.'
        runner._handle_btw_command.assert_awaited_once_with(event)
        running_agent.interrupt.assert_not_called()
        assert runner._pending_messages == {}
