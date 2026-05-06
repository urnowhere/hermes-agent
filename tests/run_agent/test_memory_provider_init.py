"""Regression tests for memory provider selection during AIAgent init."""

from types import SimpleNamespace
from unittest.mock import patch


class _FakeMemoryProvider:
    name = "fake"

    def __init__(self):
        self.initialized_with = None

    def is_available(self):
        return True

    def initialize(self, session_id, **kwargs):
        self.initialized_with = {"session_id": session_id, **kwargs}

    def get_tool_schemas(self):
        return []


def test_blank_memory_provider_does_not_auto_enable_honcho():
    """Blank memory.provider should remain opt-out even if Honcho fallback looks configured."""
    cfg = {"memory": {"provider": ""}, "agent": {}}
    honcho_cfg = SimpleNamespace(enabled=True, api_key="dummy", base_url=None)

    with (
        patch("hermes_cli.config.load_config", return_value=cfg),
        patch("hermes_cli.config.save_config") as save_config,
        patch(
            "plugins.memory.honcho.client.HonchoClientConfig.from_global_config",
            return_value=honcho_cfg,
        ) as from_global_config,
        patch("plugins.memory.load_memory_provider") as load_memory_provider,
        patch("agent.model_metadata.get_model_context_length", return_value=204_800),
        patch("run_agent.get_tool_definitions", return_value=[]),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        from run_agent import AIAgent

        agent = AIAgent(
            api_key="dummy",
            base_url="https://openrouter.ai/api/v1",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=False,
        )

    assert agent._memory_manager is None
    from_global_config.assert_not_called()
    load_memory_provider.assert_not_called()
    save_config.assert_not_called()


def test_gateway_chat_topic_is_forwarded_to_memory_provider():
    """Gateway channel topics can carry project paths for context-prefetch providers."""
    cfg = {"memory": {"provider": "fake"}, "agent": {}}
    provider = _FakeMemoryProvider()

    with (
        patch("hermes_cli.config.load_config", return_value=cfg),
        patch("plugins.memory.load_memory_provider", return_value=provider) as load_memory_provider,
        patch("agent.model_metadata.get_model_context_length", return_value=204_800),
        patch("run_agent.get_tool_definitions", return_value=[]),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        from run_agent import AIAgent

        agent = AIAgent(
            api_key="dummy",
            base_url="https://openrouter.ai/api/v1",
            platform="discord",
            chat_name="server / #hermes",
            chat_topic="Project: /tmp/example-project",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=False,
        )

    load_memory_provider.assert_called_once_with("fake")
    assert agent._memory_manager is not None
    assert provider.initialized_with["platform"] == "discord"
    assert provider.initialized_with["chat_name"] == "server / #hermes"
    assert provider.initialized_with["chat_topic"] == "Project: /tmp/example-project"

