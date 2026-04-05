"""Regression tests for the restored /model command."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent
from gateway.session import SessionEntry, SessionSource
from hermes_cli.commands import GATEWAY_KNOWN_COMMANDS, gateway_help_lines, resolve_command, telegram_bot_commands


class TestModelCommandRegistry:
    def test_model_command_resolves_and_is_known_to_gateway(self):
        cmd = resolve_command("model")
        assert cmd is not None
        assert cmd.name == "model"
        assert "model" in GATEWAY_KNOWN_COMMANDS

    def test_model_command_appears_in_gateway_help_and_telegram_menu(self):
        help_text = "\n".join(gateway_help_lines())
        assert "`/model" in help_text

        names = {name for name, _ in telegram_bot_commands()}
        assert "model" in names


def _make_runner():
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="***")}
    )
    runner.adapters = {}
    runner._voice_mode = {}
    runner.hooks = SimpleNamespace(emit=AsyncMock(), loaded_hooks=False)
    runner.session_store = MagicMock()
    runner.session_store.get_or_create_session.return_value = SessionEntry(
        session_key="agent:main:telegram:dm:c1:u1",
        session_id="sess-1",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=Platform.TELEGRAM,
        chat_type="dm",
    )
    runner.session_store.load_transcript.return_value = []
    runner.session_store.has_any_sessions.return_value = True
    runner.session_store.append_to_transcript = MagicMock()
    runner.session_store.rewrite_transcript = MagicMock()
    runner._running_agents = {}
    runner._pending_messages = {}
    runner._pending_approvals = {}
    runner._session_db = None
    runner._reasoning_config = None
    runner._provider_routing = {}
    runner._fallback_model = None
    runner._show_reasoning = False
    runner._is_user_authorized = lambda _source: True
    runner._set_session_env = lambda _context: None
    runner._run_agent = AsyncMock(
        return_value={
            "final_response": "agent-ran",
            "messages": [],
            "tools": [],
            "history_offset": 0,
            "last_prompt_tokens": 0,
        }
    )
    return runner


def _make_event(text="/model"):
    return MessageEvent(
        text=text,
        source=SessionSource(
            platform=Platform.TELEGRAM,
            user_id="u1",
            chat_id="c1",
            user_name="tester",
            chat_type="dm",
        ),
        message_id="m1",
    )


@pytest.mark.asyncio
async def test_gateway_dispatches_model_command_without_falling_through_to_agent(monkeypatch):
    import gateway.run as gateway_run

    runner = _make_runner()
    event = _make_event("/model claude-sonnet-4-6")

    monkeypatch.setattr(gateway_run, "_resolve_runtime_agent_kwargs", lambda: {"api_key": "***"})
    monkeypatch.setattr(
        "agent.model_metadata.get_model_context_length",
        lambda *_args, **_kwargs: 100_000,
    )

    with patch.object(runner, "_handle_model_command", AsyncMock(return_value="switched")) as mock_handler:
        result = await runner._handle_message(event)

    assert result == "switched"
    mock_handler.assert_awaited_once_with(event)
    runner._run_agent.assert_not_called()
