"""Tests for primary runtime state sync after provider routing.

When AIAgent routes through resolve_provider_client (at ~line 1060),
the resolved model and base_url must be synced back to the agent
BEFORE _primary_runtime is snapshotted (~line 1679), so the snapshot
captures the correct routed values.

See: TASKS.md — Issue #12078
"""

from unittest.mock import MagicMock, patch

import pytest

from run_agent import AIAgent


def _make_tool_defs(*names: str) -> list:
    """Build minimal tool definition list accepted by AIAgent.__init__."""
    return [
        {
            "type": "function",
            "function": {
                "name": n,
                "description": f"{n} tool",
                "parameters": {"type": "object", "properties": {}},
            },
        }
        for n in names
    ]


def _mock_resolve_provider_client(provider, model, raw_codex, **kw):
    """Return a mock client with known base_url and resolved model."""
    mock_client = MagicMock()
    mock_client.api_key = "routed-key-1234"
    mock_client.base_url = "https://api.example.com/v1"
    return mock_client, "claude-sonnet-4-6"


class TestPrimaryRuntimeSyncAfterRouting:
    """Verify model and base_url are synced from provider routing before _primary_runtime snapshot."""

    def test_syncs_resolved_model_when_empty(self):
        """When model is empty, the resolved model from routing should be synced.

        The routing path is only triggered when api_key and base_url are not
        both provided (line 1033: if api_key and base_url). To exercise routing,
        we provide only the api_key, leaving base_url out.
        """
        with (
            patch("run_agent.get_tool_definitions", return_value=_make_tool_defs("web_search")),
            patch("run_agent.check_toolset_requirements", return_value={}),
            patch("run_agent.OpenAI"),
            patch(
                "agent.auxiliary_client.resolve_provider_client",
                side_effect=_mock_resolve_provider_client,
            ) as mock_resolve,
        ):
            # Create agent with empty model and NO base_url — routing path will be used
            agent = AIAgent(
                api_key="test-key-1234567890",
                model="",
                quiet_mode=True,
                skip_context_files=True,
                skip_memory=True,
            )
            agent.client = MagicMock()

        # Verify resolve_provider_client was called (routing happened)
        mock_resolve.assert_called()
        # The resolved model should have been synced to agent.model
        assert agent.model == "claude-sonnet-4-6", (
            "agent.model should be synced from routing when original model is empty"
        )

    def test_syncs_base_url_when_empty(self):
        """When base_url is None, the routed client's base_url should be synced."""
        with (
            patch("run_agent.get_tool_definitions", return_value=_make_tool_defs("web_search")),
            patch("run_agent.check_toolset_requirements", return_value={}),
            patch("run_agent.OpenAI"),
            patch(
                "agent.auxiliary_client.resolve_provider_client",
                side_effect=_mock_resolve_provider_client,
            ) as mock_resolve,
        ):
            agent = AIAgent(
                api_key="test-key-1234567890",
                base_url=None,
                quiet_mode=True,
                skip_context_files=True,
                skip_memory=True,
            )
            agent.client = MagicMock()

        # Verify routing happened
        mock_resolve.assert_called()
        # The routed base_url should have been synced to agent.base_url
        assert agent.base_url == "https://api.example.com/v1", (
            "agent.base_url should be synced from routed client when original base_url is None"
        )

    def test_does_not_overwrite_explicit_model(self):
        """When model is explicitly set, it must not be overwritten by routing."""
        with (
            patch("run_agent.get_tool_definitions", return_value=_make_tool_defs("web_search")),
            patch("run_agent.check_toolset_requirements", return_value={}),
            patch("run_agent.OpenAI"),
            patch(
                "agent.auxiliary_client.resolve_provider_client",
                side_effect=_mock_resolve_provider_client,
            ),
        ):
            agent = AIAgent(
                api_key="test-key-1234567890",
                base_url="https://openrouter.ai/api/v1",
                model="gpt-4o",
                quiet_mode=True,
                skip_context_files=True,
                skip_memory=True,
            )
            agent.client = MagicMock()

        # Explicit model should be preserved — not overwritten by routing
        assert agent.model == "gpt-4o", (
            "agent.model should NOT be overwritten when explicitly set"
        )
