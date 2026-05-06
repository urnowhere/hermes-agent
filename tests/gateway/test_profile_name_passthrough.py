"""
Tests that profile_name flows correctly through the message handling pipeline.

Regression test for: NameError: name '_profile_name' is not defined
The _handle_message_with_agent method receives profile_name as a parameter
but was incorrectly referencing the parent scope's _profile_name when
calling _run_agent.
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from gateway.config import Platform
from gateway.profile_routing import ProfileRoute, parse_profile_routes


def _make_source(
    platform="discord",
    chat_id="999",
    thread_id=None,
    user_id="user1",
    chat_type="group",
    parent_chat_id=None,
):
    src = MagicMock()
    src.platform = Platform(platform)
    src.chat_id = chat_id
    src.thread_id = thread_id
    src.user_id = user_id
    src.chat_type = chat_type
    src.parent_chat_id = parent_chat_id
    src.user_name = "tester"
    return src


def _make_event(source=None, text="hello", command=None):
    event = MagicMock()
    event.source = source or _make_source()
    event.text = text
    event.internal = False
    event.message_id = "msg-1"
    event.channel_prompt = None
    event.get_command = MagicMock(return_value=command)
    event.attachments = []
    return event


def _make_runner():
    from gateway.run import GatewayRunner

    runner = GatewayRunner.__new__(GatewayRunner)
    runner._running_agents = {}
    runner._running_agents_ts = {}
    runner._busy_ack_ts = {}
    runner._run_generations = {}
    runner._session_model_overrides = {}
    runner._session_reasoning_overrides = {}
    runner._pending_model_notes = {}
    runner.adapters = {}
    runner._profile_routes_cache = None
    runner.config = MagicMock()
    runner.config.profile_routes = []
    runner.hooks = MagicMock()
    runner.hooks.emit = AsyncMock()
    runner._session_db = None

    # Session store mock
    mock_entry = MagicMock()
    mock_entry.session_id = "sess-1"
    mock_entry.session_key = "agent:main:discord:group:999:user1"
    mock_entry.created_at = 1.0
    mock_entry.updated_at = 1.0
    mock_entry.was_auto_reset = False
    mock_entry.is_fresh_reset = False

    runner.session_store = MagicMock()
    runner.session_store.get_or_create_session = MagicMock(return_value=mock_entry)
    runner.session_store.load_transcript = MagicMock(return_value=[])

    # _run_agent spy — returns a simple result dict
    runner._run_agent = AsyncMock(return_value={
        "final_response": "test reply",
        "messages": [],
        "last_prompt_tokens": 10,
        "completed": True,
    })
    runner._set_session_env = MagicMock(return_value=[])
    runner._clear_session_env = MagicMock()
    runner._begin_session_run_generation = MagicMock(return_value=1)
    runner._bind_adapter_run_generation = MagicMock()
    runner._is_session_run_current = MagicMock(return_value=True)
    runner._release_running_agent_state = MagicMock()
    runner._invalidate_session_run_generation = MagicMock()
    runner._session_key_for_source = MagicMock(
        return_value="agent:main:discord:group:999:user1"
    )
    runner._resolve_session_agent_runtime = MagicMock(
        return_value=("test-model", {})
    )
    runner._should_send_voice_reply = MagicMock(return_value=False)
    runner._resolve_gateway_model = MagicMock(return_value="test-model")
    runner._get_prefill_messages = MagicMock(return_value=[])
    runner._evict_cached_agent = MagicMock()
    runner._set_session_reasoning_override = MagicMock()
    runner._post_turn_goal_continuation = MagicMock()

    return runner


class TestProfileNameNoNameError:
    """_handle_message_with_agent must not raise NameError for _profile_name."""

    @pytest.mark.asyncio
    async def test_no_profile_route_no_error(self):
        """When no profile routes match, _run_agent receives profile_name=None."""
        runner = _make_runner()
        event = _make_event()
        source = event.source

        await runner._handle_message_with_agent(
            event, source, "key-1", 1,
            profile_name=None,
        )

        runner._run_agent.assert_called_once()
        call_kwargs = runner._run_agent.call_args
        assert call_kwargs.kwargs.get("profile_name") is None

    @pytest.mark.asyncio
    async def test_with_profile_route_no_error(self):
        """When a profile route matches, _run_agent receives the profile name."""
        runner = _make_runner()
        event = _make_event()
        source = event.source

        await runner._handle_message_with_agent(
            event, source, "key-1", 1,
            profile_name="trader",
        )

        runner._run_agent.assert_called_once()
        call_kwargs = runner._run_agent.call_args
        assert call_kwargs.kwargs.get("profile_name") == "trader"

    @pytest.mark.asyncio
    async def test_profile_name_not_undefined_variable(self):
        """The _run_agent call must use the profile_name parameter,
        not an undefined _profile_name from the parent scope.

        This is the exact regression: _handle_message_with_agent previously
        referenced _profile_name (a variable from _handle_message's scope)
        instead of its own profile_name parameter, causing NameError.
        """
        runner = _make_runner()
        event = _make_event()
        source = event.source

        # This MUST not raise NameError
        try:
            await runner._handle_message_with_agent(
                event, source, "key-1", 1,
                profile_name="trader",
            )
        except NameError as e:
            pytest.fail(f"NameError raised: {e}")


class TestProfileRoutingIntegration:
    """End-to-end: _profile_name_for_source resolves profile routes."""

    def _make_runner_with_routes(self, routes):
        runner = _make_runner()
        runner._profile_routes_cache = routes
        return runner

    def test_routed_source_returns_profile(self):
        routes = parse_profile_routes([
            {"name": "trader", "platform": "discord", "profile": "trader",
             "chat_id": "123"},
        ])
        runner = self._make_runner_with_routes(routes)
        source = _make_source(chat_id="123")
        result = runner._profile_name_for_source(source)
        assert result == "trader"

    def test_unrouted_source_returns_none(self):
        routes = parse_profile_routes([
            {"name": "trader", "platform": "discord", "profile": "trader",
             "chat_id": "123"},
        ])
        runner = self._make_runner_with_routes(routes)
        source = _make_source(chat_id="456")
        result = runner._profile_name_for_source(source)
        assert result is None

    def test_no_routes_returns_none(self):
        runner = _make_runner()
        runner._profile_routes_cache = []
        source = _make_source()
        result = runner._profile_name_for_source(source)
        assert result is None

    def test_thread_inherits_parent_route(self):
        routes = parse_profile_routes([
            {"name": "trader", "platform": "discord", "profile": "trader",
             "chat_id": "100"},
        ])
        runner = self._make_runner_with_routes(routes)
        # Thread with parent_chat_id matching the route
        source = _make_source(chat_id="200", thread_id="300", parent_chat_id="100")
        result = runner._profile_name_for_source(source)
        assert result == "trader"

    def test_different_channels_different_profiles(self):
        routes = parse_profile_routes([
            {"name": "trader-ch", "platform": "discord", "profile": "trader",
             "chat_id": "100"},
            {"name": "coder-ch", "platform": "discord", "profile": "coder",
             "chat_id": "200"},
        ])
        runner = self._make_runner_with_routes(routes)

        trader_source = _make_source(chat_id="100")
        coder_source = _make_source(chat_id="200")
        default_source = _make_source(chat_id="999")

        assert runner._profile_name_for_source(trader_source) == "trader"
        assert runner._profile_name_for_source(coder_source) == "coder"
        assert runner._profile_name_for_source(default_source) is None
